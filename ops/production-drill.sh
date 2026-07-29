#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

PUBLIC_URL="${PUBLIC_URL:-https://89-167-101-93.sslip.io}"
POST_REBOOT=false
[[ "${1:-}" == "--post-reboot" ]] && POST_REBOOT=true

blogger_running() {
  docker ps --filter 'name=bloger_tg_bot-' --format '{{.Names}}' | wc -l |
    awk '{print $1}'
}

wait_healthy() {
  local service="$1"
  local deadline=$((SECONDS + 180))
  local container_id state
  while true; do
    container_id="$(compose ps -q "$service")"
    if [[ -z "$container_id" ]]; then
      (( SECONDS < deadline )) || fail "missing service: $service"
      sleep 2
      continue
    fi
    state="$(docker inspect --format \
      '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "$container_id")"
    [[ "$state" == "healthy" || "$state" == "running" ]] && return
    [[ "$state" != "unhealthy" ]] || fail "unhealthy service: $service"
    (( SECONDS < deadline )) || fail "health timeout: $service"
    sleep 2
  done
}

blogger_before="$(blogger_running)"
(( blogger_before >= 2 )) || fail "Blogger Bot baseline is not healthy"

for service in api bot relay worker postgres redis nginx; do
  count="$(compose ps -q "$service" | awk 'NF {count++} END {print count+0}')"
  [[ "$count" == "1" ]] || fail "singleton count failed: $service"
done
for service in postgres redis clamav api nginx; do
  wait_healthy "$service"
done

curl --fail --silent --show-error --max-time 10 \
  "${PUBLIC_URL}/health/ready" >/dev/null
headers="$(curl --fail --silent --show-error --head --max-time 10 "$PUBLIC_URL")"
grep -qi '^strict-transport-security:' <<<"$headers" ||
  fail "HSTS header is missing"
status "HTTPS edge" "passed"

published="$(compose config --format json |
  python3 -c 'import json,sys
d=json.load(sys.stdin)
bad=[]
for name,service in d["services"].items():
    if name != "nginx" and service.get("ports"):
        bad.append(name)
raise SystemExit(1 if bad else 0)')"
[[ -z "$published" ]] || fail "private service exposure check failed"
status "Private services" "passed"

if [[ "$POST_REBOOT" == false ]]; then
  active_jobs="$(postgres_shell 'psql --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" --tuples-only --no-align --command \
    "SELECT count(*) FROM generation_jobs \
    WHERE status NOT IN ('"'"'succeeded'"'"','"'"'failed'"'"');"')"
  [[ "$active_jobs" == "0" ]] || fail "active generation jobs block fault drills"

  compose restart redis
  wait_healthy redis
  compose restart postgres
  wait_healthy postgres
  wait_healthy api
  worker_id="$(compose ps -q worker)"
  [[ -n "$worker_id" ]] || fail "worker is not running before termination drill"
  compose kill --signal SIGTERM worker
  docker wait "$worker_id" >/dev/null
  compose up -d worker
  wait_healthy worker
  status "Dependency/worker restart" "passed"

  certbot renew --dry-run --cert-name 89-167-101-93.sslip.io
  status "TLS renewal dry run" "passed"

  "${OPS_DIR}/backup-postgres.sh"
  "${OPS_DIR}/restore-drill.sh"
  PRIVACY_CANARY="car-wrap-privacy-$(date -u +%s)-$$" \
    "${OPS_DIR}/privacy-scan.sh"
fi

[[ "$(blogger_running)" == "$blogger_before" ]] ||
  fail "Blogger Bot baseline changed"
status "Blogger Bot baseline" "unchanged"
status "Production drill" "passed"

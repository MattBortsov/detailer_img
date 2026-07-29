#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

EXPECTED_SHA="${1:-}"
PREVIOUS_SHA="${2:-}"
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "expected SHA must be full"
[[ "$(git -C "$PROJECT_ROOT" rev-parse HEAD)" == "$EXPECTED_SHA" ]] ||
  fail "working tree is not at the expected SHA"

rollback() {
  local exit_code=$?
  trap - ERR
  if [[ "$PREVIOUS_SHA" =~ ^[0-9a-f]{40}$ && "$PREVIOUS_SHA" != "$EXPECTED_SHA" ]]; then
    status "Deployment" "failed; rolling source back"
    git -C "$PROJECT_ROOT" reset --hard "$PREVIOUS_SHA" >/dev/null
    git -C "$PROJECT_ROOT" clean -fd >/dev/null
    compose config --quiet
    compose build
    compose up -d --remove-orphans
  fi
  exit "$exit_code"
}
trap rollback ERR

"${OPS_DIR}/preflight.sh"
compose pull postgres redis clamav nginx
compose build --pull
compose up -d --remove-orphans

deadline=$((SECONDS + 660))
for service in postgres redis clamav api nginx; do
  container_id="$(compose ps -q "$service")"
  [[ -n "$container_id" ]] || fail "service did not start: $service"
  while true; do
    state="$(docker inspect --format \
      '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "$container_id")"
    [[ "$state" == "healthy" || "$state" == "running" ]] && break
    [[ "$state" != "unhealthy" ]] || fail "service is unhealthy: $service"
    (( SECONDS < deadline )) || fail "service health timeout: $service"
    sleep 2
  done
done

for service in api bot relay worker postgres redis nginx; do
  count="$(compose ps -q "$service" | awk 'NF {count++} END {print count+0}')"
  [[ "$count" == "1" ]] || fail "singleton count failed: $service"
done

curl --fail --silent --show-error --max-time 10 \
  https://89-167-101-93.sslip.io/health/ready >/dev/null
"${OPS_DIR}/install-backup-timer.sh"
status "Deployment" "healthy at ${EXPECTED_SHA:0:12}"
trap - ERR

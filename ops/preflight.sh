#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

PUBLIC_HOST="${PUBLIC_HOST:-89-167-101-93.sslip.io}"
BOOTSTRAP_MODE=false
[[ "${1:-}" == "--bootstrap" ]] && BOOTSTRAP_MODE=true

require_command docker
require_command awk
require_command curl
require_command stat
[[ -f "$COMPOSE_FILE" ]] || fail "Compose file is missing"
[[ -f "$ENV_FILE" ]] || fail "application .env is missing"

permissions="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")"
[[ "$permissions" == "600" ]] || fail ".env must have mode 0600"
status ".env permissions" "ok"

require_env_names \
  POSTGRES_PASSWORD \
  DATABASE_URL \
  TELEGRAM_BOT_TOKEN \
  TELEGRAM_BOT_USERNAME \
  MINI_APP_URL \
  OPENROUTER_API_KEY

compose config --quiet
status "Compose configuration" "ok"

if command -v getent >/dev/null 2>&1; then
  getent ahostsv4 "$PUBLIC_HOST" | awk '{print $1}' | grep -Fxq '89.167.101.93' ||
    fail "public hostname does not resolve to the Hetzner address"
fi
status "Public hostname" "ok"

if [[ -r /proc/meminfo ]]; then
  memory_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  (( memory_kib >= 3500000 )) || fail "at least 3.5 GiB RAM is required"
  status "Host memory" "ok"
  if [[ "$BOOTSTRAP_MODE" == false ]]; then
    swap_kib="$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)"
    (( swap_kib >= 1900000 )) || fail "at least 2 GiB swap is required"
    status "Host swap" "ok"
  fi
fi

available_kib="$(df -Pk "$PROJECT_ROOT" | awk 'NR == 2 {print $4}')"
(( available_kib >= 5 * 1024 * 1024 )) || fail "at least 5 GiB disk is required"
status "Free disk" "ok"

if [[ "$BOOTSTRAP_MODE" == false ]]; then
  cert_dir="/etc/letsencrypt/live/${PUBLIC_HOST}"
  [[ -r "${cert_dir}/fullchain.pem" && -r "${cert_dir}/privkey.pem" ]] ||
    fail "TLS certificate is missing"
  status "TLS certificate" "ok"
fi

status "Preflight" "passed"

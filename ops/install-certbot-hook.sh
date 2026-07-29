#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

PUBLIC_HOST="${PUBLIC_HOST:-89-167-101-93.sslip.io}"
CERT_DIR="/etc/letsencrypt/live/${PUBLIC_HOST}"
HOOK_PATH="/etc/letsencrypt/renewal-hooks/deploy/detailer-img-nginx"
[[ "$(id -u)" == "0" ]] || fail "certificate setup requires root"
require_command certbot
require_command ss

if [[ ! -r "${CERT_DIR}/fullchain.pem" || ! -r "${CERT_DIR}/privkey.pem" ]]; then
  ss -ltnH 'sport = :80' | grep -q . && fail "port 80 is already in use"
  certbot certonly --standalone --non-interactive --agree-tos \
    --register-unsafely-without-email --cert-name "$PUBLIC_HOST" -d "$PUBLIC_HOST"
fi

install -d -m 0755 "$(dirname -- "$HOOK_PATH")"
hook_tmp="$(mktemp)"
trap 'rm -f -- "$hook_tmp"' EXIT
{
  printf '%s\n' '#!/usr/bin/env bash' 'set -Eeuo pipefail'
  printf '%s\n' 'case " ${RENEWED_DOMAINS:-} " in'
  printf '%s\n' '  *" 89-167-101-93.sslip.io "*) ;;'
  printf '%s\n' '  *) exit 0 ;;'
  printf '%s\n' 'esac'
  printf '%s\n' 'cd /root/detailer_img'
  printf '%s\n' 'docker compose ps --status running nginx --quiet | grep -q . || exit 0'
  printf '%s\n' 'docker compose exec -T nginx nginx -t'
  printf '%s\n' 'docker compose exec -T nginx nginx -s reload'
} >"$hook_tmp"
install -m 0755 "$hook_tmp" "$HOOK_PATH"
status "TLS certificate and hook" "ready"

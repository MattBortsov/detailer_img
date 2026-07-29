#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/detailer-img}"
canary="${PRIVACY_CANARY:-}"
[[ -n "$canary" ]] || fail "PRIVACY_CANARY is required"
(( ${#canary} >= 12 && ${#canary} <= 256 )) || fail "invalid canary length"

patterns=("$canary" "data:image/" '"b64_json"')
services=(api bot relay worker)

for service in "${services[@]}"; do
  for pattern in "${patterns[@]}"; do
    if compose logs --since 24h --no-color "$service" 2>/dev/null |
      grep -F -- "$pattern" >/dev/null; then
      fail "privacy scan found forbidden content in ${service} logs"
    fi
  done
done
status "Container logs" "clean"

for pattern in "${patterns[@]}"; do
  if postgres_shell 'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --format=plain --no-owner --no-acl' | grep -F -- "$pattern" >/dev/null; then
    fail "privacy scan found forbidden content in PostgreSQL"
  fi
done
status "PostgreSQL metadata" "clean"

if compose exec -T redis redis-cli --scan | grep -F -- "$canary" >/dev/null; then
  fail "privacy scan found canary in Redis keys"
fi
status "Redis keys" "clean"

latest="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'car-wrap-*.dump' \
  -print 2>/dev/null | sort | tail -1)"
if [[ -n "$latest" ]]; then
  for pattern in "${patterns[@]}"; do
    if compose exec -T postgres pg_restore --file=- <"$latest" |
      grep -F -- "$pattern" >/dev/null; then
      fail "privacy scan found forbidden content in metadata backup"
    fi
  done
  status "Metadata backup" "clean"
fi

status "Privacy scan" "passed"

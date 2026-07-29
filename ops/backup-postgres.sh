#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/detailer-img}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
require_narrow_absolute_dir "$BACKUP_DIR"
[[ "$RETENTION_DAYS" =~ ^[0-9]{1,3}$ ]] || fail "invalid retention"
(( RETENTION_DAYS >= 1 && RETENTION_DAYS <= 365 )) || fail "invalid retention"

install -d -m 0700 "$BACKUP_DIR"
umask 077
stamp="$(date -u +'%Y%m%dT%H%M%SZ')"
final_path="${BACKUP_DIR}/car-wrap-${stamp}.dump"
partial_path="${final_path}.partial"
[[ ! -e "$final_path" && ! -e "$partial_path" ]] || fail "backup target exists"
trap 'rm -f -- "$partial_path"' EXIT

postgres_shell 'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom' \
  >"$partial_path"
[[ -s "$partial_path" ]] || fail "backup archive is empty"
compose exec -T postgres pg_restore --list <"$partial_path" >/dev/null
chmod 0600 "$partial_path"
mv -- "$partial_path" "$final_path"
trap - EXIT

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'car-wrap-*.dump' \
  -mtime "+${RETENTION_DAYS}" -delete
status "Metadata backup" "$(basename -- "$final_path")"

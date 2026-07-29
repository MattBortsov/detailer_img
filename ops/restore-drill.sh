#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/detailer-img}"
require_narrow_absolute_dir "$BACKUP_DIR"
archive="${1:-}"
if [[ -z "$archive" ]]; then
  archive="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'car-wrap-*.dump' \
    -print | sort | tail -1)"
fi
[[ -n "$archive" && -f "$archive" ]] || fail "backup archive is missing"
case "$archive" in
  "$BACKUP_DIR"/car-wrap-*.dump) ;;
  *) fail "backup archive is outside the allowed directory" ;;
esac

restore_db="car_wrap_restore_$(date -u +'%Y%m%d%H%M%S')_$$"
[[ "$restore_db" =~ ^car_wrap_restore_[0-9]+_[0-9]+$ ]] ||
  fail "unsafe restore database name"

cleanup() {
  postgres_shell 'dropdb --if-exists --username "$POSTGRES_USER" "$1"' "$restore_db" \
    >/dev/null 2>&1 || true
}
trap cleanup EXIT

postgres_shell 'createdb --username "$POSTGRES_USER" --template template0 "$1"' \
  "$restore_db"
postgres_shell 'pg_restore --username "$POSTGRES_USER" --dbname "$1" \
    --exit-on-error --no-owner --no-acl' "$restore_db" <"$archive"

table_count="$(postgres_shell 'psql --username "$POSTGRES_USER" --dbname "$1" \
  --tuples-only --no-align --command \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema = '"'"'public'"'"';"' \
  "$restore_db")"
[[ "$table_count" =~ ^[0-9]+$ ]] || fail "restore table check failed"
(( table_count > 0 )) || fail "restored database has no application tables"
status "Restore drill" "passed (${table_count} tables)"

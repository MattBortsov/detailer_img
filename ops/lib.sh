#!/usr/bin/env bash

set -Eeuo pipefail

OPS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "${OPS_DIR}/.." && pwd -P)"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_ROOT}/docker-compose.yml}"
ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env}"

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

status() {
  printf '%-32s %s\n' "$1" "$2"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"
}

require_narrow_absolute_dir() {
  local target="$1"
  [[ "$target" == /* ]] || fail "directory must be absolute"
  [[ "$target" != *"//"* && "$target" != */. && "$target" != *"/./"* ]] ||
    fail "directory must be normalized"
  [[ "$target" != */.. && "$target" != *"/../"* ]] ||
    fail "directory traversal is not allowed"
  case "$target" in
    / | /root | /home | /var | /var/backups) fail "directory target is too broad" ;;
  esac
  [[ "$target" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "directory has unsafe characters"
}

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

env_name_is_set() {
  local name="$1"
  awk -F= -v key="$name" '
    $0 !~ /^[[:space:]]*#/ && $1 == key && length(substr($0, index($0, "=") + 1)) > 0 {
      found=1
    }
    END { exit(found ? 0 : 1) }
  ' "$ENV_FILE"
}

require_env_names() {
  local missing=0
  local name
  for name in "$@"; do
    if env_name_is_set "$name"; then
      status "$name" "set"
    else
      status "$name" "MISSING"
      missing=1
    fi
  done
  (( missing == 0 )) || fail "required application settings are missing"
}

postgres_shell() {
  compose exec -T postgres sh -ec "$1" sh "${@:2}"
}

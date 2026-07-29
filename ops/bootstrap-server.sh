#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

[[ "$(id -u)" == "0" ]] || fail "server bootstrap requires root"
[[ -r /etc/os-release ]] || fail "unsupported host"
grep -q '^ID=ubuntu$' /etc/os-release || fail "Ubuntu host required"
require_command docker
docker compose version >/dev/null

BACKUP_DIR="${BACKUP_DIR:-/var/backups/detailer-img}"
require_narrow_absolute_dir "$BACKUP_DIR"
install -d -m 0700 "$BACKUP_DIR"
status "Backup directory" "ready"

if ! swapon --show=NAME --noheadings | grep -Fxq '/swapfile'; then
  [[ ! -e /swapfile ]] || fail "/swapfile exists but is not active"
  fallocate -l 2G /swapfile
  chmod 0600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -Eq '^/swapfile[[:space:]]+none[[:space:]]+swap' /etc/fstab ||
    printf '/swapfile none swap sw 0 0\n' >>/etc/fstab
fi
status "2 GiB swap" "active"

"${OPS_DIR}/preflight.sh" --bootstrap

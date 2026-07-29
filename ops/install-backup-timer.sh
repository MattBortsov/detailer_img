#!/usr/bin/env bash

set -Eeuo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib.sh"

SERVICE_PATH="/etc/systemd/system/detailer-img-backup.service"
TIMER_PATH="/etc/systemd/system/detailer-img-backup.timer"
[[ "$(id -u)" == "0" ]] || fail "backup timer setup requires root"
require_command systemctl
[[ -d /run/systemd/system ]] || fail "systemd is not running"
[[ "$PROJECT_ROOT" == "/root/detailer_img" ]] ||
  fail "production project path must be /root/detailer_img"

service_tmp="$(mktemp)"
timer_tmp="$(mktemp)"
trap 'rm -f -- "$service_tmp" "$timer_tmp"' EXIT

{
  printf '%s\n' \
    '[Unit]' \
    'Description=Detailer Img PostgreSQL metadata backup' \
    'Requires=docker.service' \
    'After=docker.service' \
    'ConditionPathExists=/root/detailer_img/.env' \
    '' \
    '[Service]' \
    'Type=oneshot' \
    'User=root' \
    'Group=root' \
    'WorkingDirectory=/root/detailer_img' \
    'UMask=0077' \
    'ExecStart=/root/detailer_img/ops/backup-postgres.sh' \
    'NoNewPrivileges=true' \
    'PrivateTmp=true' \
    'ProtectHome=read-only' \
    'ProtectSystem=strict' \
    'ReadWritePaths=/var/backups/detailer-img' \
    'ProtectKernelTunables=true' \
    'ProtectKernelModules=true' \
    'ProtectControlGroups=true' \
    'RestrictSUIDSGID=true' \
    'LockPersonality=true'
} >"$service_tmp"

{
  printf '%s\n' \
    '[Unit]' \
    'Description=Daily Detailer Img PostgreSQL metadata backup' \
    '' \
    '[Timer]' \
    'OnCalendar=*-*-* 03:30:00 UTC' \
    'RandomizedDelaySec=30m' \
    'Persistent=true' \
    'Unit=detailer-img-backup.service' \
    '' \
    '[Install]' \
    'WantedBy=timers.target'
} >"$timer_tmp"

install -m 0644 "$service_tmp" "$SERVICE_PATH"
install -m 0644 "$timer_tmp" "$TIMER_PATH"
systemctl daemon-reload
systemctl enable --now detailer-img-backup.timer >/dev/null
systemctl is-enabled --quiet detailer-img-backup.timer
systemctl is-active --quiet detailer-img-backup.timer
status "Daily metadata backup" "timer active"

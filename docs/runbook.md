# CarWrap production runbook

## Boundaries

CarWrap runs as the `detailer-img` Compose project in `/root/detailer_img`.
The Blogger Bot is a separate project and must not be stopped, recreated or
attached to CarWrap networks. CarWrap publishes only Nginx 443 during normal
runtime. PostgreSQL, Redis, API, bot, relay, worker and ClamAV remain private.

Vehicle source/result bytes are memory-only. Routine backups contain PostgreSQL
metadata, not custom-color reference files or Docker volumes.

## Daily checks

```bash
cd /root/detailer_img
./ops/preflight.sh
docker compose ps
curl --fail --silent https://89-167-101-93.sslip.io/health/ready
```

Use scoped recent logs; never run commands that print container environments:

```bash
docker compose logs --since 30m --tail 200 api bot daily-stats relay worker
```

## Daily administrator statistics

The `daily-stats` service sends each configured `ADMIN_TELEGRAM_USER_IDS`
one report for the preceding UTC calendar day. By default it is sent after
09:00 UTC; set `DAILY_STATS_HOUR_UTC` in `.env` to change the hour. Delivery
is recorded in PostgreSQL per administrator, so a normal service restart does
not resend an already confirmed report.

```bash
docker compose logs --since 26h --tail 100 daily-stats
```

## Backup and isolated restore

```bash
cd /root/detailer_img
./ops/backup-postgres.sh
./ops/restore-drill.sh
```

Backups are mode `0600` under `/var/backups/detailer-img` and expire after the
configured bounded retention. The restore drill creates and drops a separate
`car_wrap_restore_*` database; it never writes to production.

The production deployment installs a persistent systemd timer that runs the
metadata backup daily at 03:30 UTC with a randomized delay of up to 30 minutes.

```bash
systemctl status detailer-img-backup.timer --no-pager
systemctl list-timers detailer-img-backup.timer --no-pager
```

## Certificate renewal

The existing host `certbot.timer` performs renewal. The CarWrap deploy hook
tests and gracefully reloads only the Nginx container after this certificate is
renewed.

```bash
systemctl status certbot.timer --no-pager
certbot renew --dry-run --cert-name 89-167-101-93.sslip.io
```

## Operational drills

```bash
cd /root/detailer_img
./ops/production-drill.sh
```

The runner verifies singleton services, HTTPS, private ports, Redis redelivery
recovery, PostgreSQL restart recovery, idle worker termination, certificate
renewal, privacy canaries, metadata backup and isolated restore. Duplicate
submission and provider/delivery ambiguity are covered by the deterministic
integration suite; the production drill deliberately performs no paid
generation and refuses to restart the worker while a job is active.

## Incident triage

1. Check `docker compose ps` and `/health/live` versus `/health/ready`.
2. If Redis is unavailable, keep PostgreSQL and the worker running; PostgreSQL
   polling remains authoritative.
3. If a provider upload outcome is ambiguous, never regenerate automatically.
   Preserve the recorded stable state and follow the user recovery message.
4. If Telegram delivery is ambiguous after process loss, never regenerate to
   repair it.
5. If PostgreSQL is unavailable, restore service first; do not accept or
   manually rewrite job states.

## Rollback

CD records the prior Git revision. If build/start/health fails,
`ops/deploy.sh` resets source to that exact revision and rebuilds the previous
application image. It never rolls database data back automatically.

Manual rollback requires a known CI-validated full SHA:

```bash
cd /root/detailer_img
git fetch origin main
git reset --hard <known-good-full-sha>
./ops/deploy.sh <known-good-full-sha> <current-full-sha>
```

Do not use a broad cleanup command and do not remove `.env` or named volumes.

## Host reboot checkpoint

A reboot briefly interrupts both CarWrap and Blogger Bot. Run it only after the
normal production drill passes and the user approves the shared-host outage.
After SSH returns:

```bash
cd /root/detailer_img
./ops/production-drill.sh --post-reboot
```

The phase is complete only when Docker, both Blogger Bot containers, every
CarWrap singleton, PostgreSQL/Redis readiness, HTTPS and the certificate recover
automatically.

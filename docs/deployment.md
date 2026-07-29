# GitHub CI/CD and Hetzner deployment

Repository: `https://github.com/MattBortsov/detailer_img.git`.

## GitHub secret

Only one repository secret is required:

| Name | Value |
|---|---|
| `DEPLOY_SSH_KEY` | Existing Mac SSH private key accepted by Hetzner |

Application settings are not GitHub secrets. They remain only in
`/root/detailer_img/.env` with mode `0600`.

## Production

- Host: `89.167.101.93`
- Path: `/root/detailer_img`
- Mini App: `https://89-167-101-93.sslip.io`
- Compose project: `detailer-img`

CI validates tests, formatting, typing, Compose, the application image and
high-severity runtime vulnerabilities. CD deploys only the exact `main` commit
that completed CI successfully. `ops/deploy.sh` validates the server, builds,
migrates once, waits for health, checks singleton counts and probes HTTPS.
After checking out that revision, CD removes untracked source files inside the
repository; `.env` remains because it is intentionally ignored by Git.

Initial host preparation:

```bash
cd /root/detailer_img
chmod 0600 .env
./ops/bootstrap-server.sh
./ops/install-certbot-hook.sh
./ops/preflight.sh
```

Normal deployments are automatic after a successful push to `main`. A manual
deployment also installs the daily metadata-backup timer. A manual server
health check is:

```bash
cd /root/detailer_img
docker compose ps
curl --fail --silent https://89-167-101-93.sslip.io/health/ready
systemctl status detailer-img-backup.timer --no-pager
```

Never print `.env`, container environment arrays or raw database URLs.
See `docs/runbook.md` for backup, restore, incident and rollback procedures.

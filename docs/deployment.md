# GitHub CI/CD and Hetzner deployment

Repository: `https://github.com/MattBortsov/detailer_img.git`.

## What to add to GitHub

Open **Settings → Secrets and variables → Actions → New repository secret**.
Add only one secret:

| Name | Value |
|---|---|
| `DEPLOY_SSH_KEY` | Contents of the Mac SSH key already accepted by Hetzner |

Copy its value on the Mac:

```bash
pbcopy < ~/.ssh/id_ed25519
```

Paste the clipboard into `DEPLOY_SSH_KEY`.

Nothing else is required in GitHub: no `DEPLOY_KNOWN_HOSTS`, `APP_ENV`, or
`DEPLOY_ENABLED`.

## Server

The existing Blogger Bot credential is reused for the server-to-GitHub
connection. Application secrets stay only in
`/root/detailer_img/.env` with mode `0600`.

After CI succeeds on `main`, CD synchronizes that exact commit to the server.
Once a Compose file is added, the same workflow also starts or updates the
containers.

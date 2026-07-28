# Detailer Image Bot

Telegram bot and Mini App for vehicle recoloring. A user sends a vehicle photo,
opens the palette, and chooses a built-in, moderated community, or AI-selected
color. The generation contract keeps vehicle geometry and identity unchanged.

## Privacy boundary

- Vehicle source files and generated results are not persisted by this
  application.
- A user-uploaded wrap-color reference is scanned, freshly rendered as a
  metadata-free PNG, stored privately, and may become visible in `User Colors`
  after moderation.
- ClamAV uses a shared Unix socket. Its TCP ports and private object volume are
  not published.

## Production configuration

Copy `.env.example` to `.env` on the server and fill the blank values.
`DATABASE_URL` must use `postgresql+psycopg` and the Compose hostname
`postgres`; percent-encode special characters in the password.

The application binds only to `127.0.0.1:8010` by default. A host reverse proxy
must terminate HTTPS and forward that public Mini App URL to this loopback
port. The existing Blogger Bot keeps port `8000`.

Custom-color limits are matched across the browser, application, and ClamAV:
one file, 8 MiB input, one frame, 20 megapixels, and a 2048 px canonical long
edge. The private volume is initialized as UID/GID `10001` with mode `0700`.

## Validate and run

```bash
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
```

The first ClamAV start may take several minutes while its persistent signature
database is prepared. Production is fail-closed: the API waits for PostgreSQL,
migrations, private storage initialization, and a healthy ClamAV daemon.

The current Hetzner host has 3.7 GiB RAM and no swap. ClamAV documents a
3 GiB minimum and can briefly use more during signature updates. The Compose
configuration disables concurrent database reload and database load-testing
to reduce peaks, but swap or a larger server is required before treating this
host as production-safe.

## Tests

Python 3.13 is required. The PostgreSQL integration suite additionally needs a
test-only database URL whose database name contains `test`:

```bash
CAR_WRAP_TEST_DATABASE_URL=postgresql+psycopg://... \
  python3.13 -m pytest -q
python3.13 -m ruff check src tests
python3.13 -m mypy --strict src
```

# Quickstart

regstack expects Python 3.11+ and `uv`. The default backend is SQLite —
**no database server required** for development. Postgres and MongoDB
are also supported (just point ``database_url`` at the right place).

## Install

```bash
uv add regstack            # production (SQLite-only by default)
uv add 'regstack[postgres]' # add Postgres support
uv add 'regstack[mongo]'    # add MongoDB support
uv sync --extra dev        # for working in this repo
```

Optional extras:

| Extra      | Pulls in       | Needed for                       |
|------------|---------------|----------------------------------|
| `postgres` | `asyncpg`     | Postgres backend                 |
| `mongo`    | `pymongo`     | MongoDB backend                  |
| `ses`      | `aioboto3`    | AWS SES email backend            |
| `sns`      | `aioboto3`    | AWS SNS SMS backend              |
| `twilio`   | `twilio`      | Twilio SMS backend               |
| `docs`     | sphinx + ext  | Building these docs              |

SQLite, SQLAlchemy and Alembic are bundled in the base install.

## Generate a config

```bash
uv run regstack init
```

The wizard:

1. Asks which backend you want (SQLite / Postgres / MongoDB).
2. Builds the right `database_url` for it (SQLite path / Postgres URL /
   Mongo URL).
3. Generates a 64-byte JWT secret.
4. Walks through email backend (`console` / SMTP / SES) and feature
   flags.

Two files land in the current directory:

- `regstack.toml` — non-sensitive settings.
- `regstack.secrets.env` — JWT secret + database URL. Mode 0600. Add
  to `.gitignore`.

The wizard never provisions infrastructure. It validates connection
URLs and runs read-only DNS sanity checks if you opt in, but it never
creates SES identities, Route 53 records, or anything similar.

## Embed in a FastAPI app

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from regstack import RegStack, RegStackConfig


config = RegStackConfig.load()
regstack = RegStack(config=config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await regstack.install_schema()
    yield
    await regstack.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(regstack.router, prefix=config.api_prefix)
```

The backend is selected automatically by the URL scheme of
`config.database_url`. Hosts that need to share a connection pool with
their own code can build a backend explicitly and pass
``RegStack(config=config, backend=my_backend)``.

That mounts:

- `POST /api/auth/register`
- `POST /api/auth/verify`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET  /api/auth/me`
- `POST /api/auth/change-password`
- `POST /api/auth/change-email`
- `POST /api/auth/confirm-email-change`
- `DELETE /api/auth/account`
- `POST /api/auth/forgot-password` (when `enable_password_reset`)
- `POST /api/auth/reset-password` (when `enable_password_reset`)
- `POST /api/auth/phone/start` (when `enable_sms_2fa`)
- `POST /api/auth/phone/confirm` (when `enable_sms_2fa`)
- `DELETE /api/auth/phone` (when `enable_sms_2fa`)
- `POST /api/auth/login/mfa-confirm` (when `enable_sms_2fa`)
- `/api/auth/admin/*` (when `enable_admin_router`)

## Add the SSR pages (optional)

```python
if config.enable_ui_router:
    app.include_router(regstack.ui_router, prefix=config.ui_prefix)
    app.mount(config.static_prefix, regstack.static_files)
```

This adds browser-facing forms at `/account/login`, `/account/register`,
`/account/me`, etc. and serves the bundled `core.css`, `theme.css`, and
`regstack.js` at `/regstack-static/`. The pages are stateless and use
the JSON API via `fetch`.

## End-to-end smoke test (zero infrastructure)

```bash
# In one terminal — boot the SQLite demo
export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
uv run uvicorn examples.sqlite.main:app --reload

# In another terminal
curl -X POST http://localhost:8000/api/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"a@example.com","password":"hunter2hunter2","full_name":"A"}'
```

The SQLite demo enables verification by default — follow the link
printed to the demo's stdout to verify, then `POST /api/auth/login` to
get a JWT. There's a Postgres demo (`examples/postgres/`) and a Mongo
demo (`examples/mongo/`) that take the same route inventory.

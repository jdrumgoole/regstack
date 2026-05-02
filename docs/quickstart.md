# Quickstart

This guide walks you from "nothing installed" to "registered user
with a verified email" in about ten minutes. You will need
Python 3.11 or newer and [`uv`](https://docs.astral.sh/uv/)
(Astral's fast Python package manager — used throughout regstack's
tooling).

The default storage backend is SQLite, so **no database server is
required for development**. The same code runs against
[PostgreSQL](https://www.postgresql.org/) or
[MongoDB](https://www.mongodb.com/) by changing one URL.

## Install

```bash
uv add regstack            # production install (SQLite-only by default)
uv add 'regstack[postgres]' # add Postgres support
uv add 'regstack[mongo]'    # add MongoDB support
uv sync --extra dev        # for working in this repo
```

The base install bundles SQLite, [SQLAlchemy](https://www.sqlalchemy.org/),
and [Alembic](https://alembic.sqlalchemy.org/) (used to manage SQL
schema migrations). Heavyweight dependencies are pulled in only when
you opt in via an extra.

| Extra      | Pulls in       | Needed for                       |
|------------|---------------|----------------------------------|
| `postgres` | `asyncpg`     | Postgres backend                 |
| `mongo`    | `pymongo`     | MongoDB backend                  |
| `ses`      | `aioboto3`    | [Amazon SES](https://aws.amazon.com/ses/) email backend |
| `sns`      | `aioboto3`    | [Amazon SNS](https://aws.amazon.com/sns/) SMS backend   |
| `twilio`   | `twilio`      | [Twilio](https://www.twilio.com/) SMS backend           |
| `docs`     | sphinx + ext  | Building these docs              |

## Generate a config

```bash
uv run regstack init
```

The wizard asks a handful of questions and writes two files:

1. Which backend do you want? (SQLite / Postgres / MongoDB)
2. Builds the right `database_url` for your choice (a SQLite path, a
   Postgres connection URL, or a Mongo URL).
3. Generates a 64-byte signing secret used to sign and verify
   [JWTs](https://datatracker.ietf.org/doc/html/rfc7519) — keep this
   secret.
4. Walks through email backend (`console` / SMTP / SES) and feature
   flags.

Output:

- `regstack.toml` — non-sensitive settings, safe to commit if you
  redact secrets.
- `regstack.secrets.env` — JWT secret and database URL. Mode `0600`.
  **Add to `.gitignore`.**

The wizard never provisions infrastructure. It validates connection
URLs and runs read-only DNS sanity checks (SPF/DKIM/MX) if you opt in,
but it never creates SES identities, Route 53 records, or anything
similar. Provisioning is your responsibility.

## Embed in a FastAPI app

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from regstack import RegStack, RegStackConfig


config = RegStackConfig.load()
regstack = RegStack(config=config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await regstack.install_schema()       # idempotent
    yield
    await regstack.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(regstack.router, prefix=config.api_prefix)
```

`RegStack` picks the right backend automatically from the URL scheme of
`config.database_url`:

- `sqlite+aiosqlite:///PATH` → SQLite via SQLAlchemy. `PATH` is the
  filename (e.g. `./dbname.db`) or the literal `:memory:`. See
  [SQLite URL forms](configuration.md#sqlite-url-forms) for the
  absolute-path variant.
- `postgresql+asyncpg://<username>:<password>@dbhost.example.com:5432/dbname`
  → Postgres via SQLAlchemy
- `mongodb://<username>:<password>@dbhost.example.com:27017/dbname`
  (or `mongodb+srv://…`) → MongoDB

`install_schema()` is idempotent. On SQL backends it runs Alembic
migrations to head; on MongoDB it ensures the indexes exist. Calling
it on every boot is the right thing to do.

The mounted router gives you:

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

If you want browser-facing forms in addition to the JSON API:

```python
if config.enable_ui_router:
    app.include_router(regstack.ui_router, prefix=config.ui_prefix)
    app.mount(config.static_prefix, regstack.static_files)
```

This adds pages at `/account/login`, `/account/register`,
`/account/me`, etc., and serves the bundled `core.css`, `theme.css`,
and `regstack.js` at `/regstack-static/`. The pages are stateless and
talk to the JSON API via `fetch`. Re-skin them by serving a single
CSS file — see [Theming](theming.md).

## End-to-end smoke test (zero infrastructure)

```bash
# In one terminal — boot the SQLite demo
export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
uv run uvicorn examples.sqlite.main:app --reload

# In another terminal
curl -X POST http://localhost:8000/api/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"alice@app.example.com","password":"<password>","full_name":"Alice"}'
```

The SQLite demo enables verification by default. Look at the demo's
stdout — the `console` email backend prints the verification link
there instead of sending a real email. Click it (or `curl` it), then
`POST /api/auth/login` to receive a JWT.

A Postgres demo (`examples/postgres/`) and a Mongo demo
(`examples/mongo/`) take the same routes. The only thing that changes
between them is `database_url`.

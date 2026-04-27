# Quickstart

regstack expects Python 3.11+, a working `uv`, and a MongoDB instance
(local or remote — `mongodb://localhost:27017` for development).

## Install

```bash
uv add regstack            # production
uv sync --extra dev        # for working in this repo
```

Optional extras:

| Extra      | Pulls in       | Needed for                       |
|------------|---------------|----------------------------------|
| `ses`      | `aioboto3`    | AWS SES email backend            |
| `sns`      | `aioboto3`    | AWS SNS SMS backend              |
| `twilio`   | `twilio`      | Twilio SMS backend               |
| `docs`     | sphinx + ext  | Building these docs              |

## Generate a config

```bash
uv run regstack init
```

The wizard writes two files in the current directory:

- `regstack.toml` — non-sensitive settings (app name, base URL, feature
  flags, MongoDB database, email/SMS provider choice).
- `regstack.secrets.env` — JWT secret, MongoDB URL, SMTP password, etc.
  Created with mode `0600`. Add this file to `.gitignore`.

The wizard never provisions infrastructure. It validates connection
URLs and runs read-only DNS sanity checks if you opt in, but it never
creates SES identities, Route 53 records, or anything similar.

## Embed in a FastAPI app

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from regstack import RegStack, RegStackConfig
from regstack.db.client import make_client


config = RegStackConfig.load()
mongo = make_client(config)
db = mongo[config.mongodb_database]
regstack = RegStack(config=config, db=db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await regstack.install_indexes()
    yield
    await mongo.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(regstack.router, prefix=config.api_prefix)
```

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

## End-to-end smoke test

```bash
# In one terminal — boot the bundled example
export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
export REGSTACK_MONGODB_URL=mongodb://localhost:27017
uv run uvicorn examples.minimal.main:app --reload

# In another terminal
curl -X POST http://localhost:8000/api/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"a@example.com","password":"hunter2hunter2","full_name":"A"}'
```

If `require_verification` is on (the default), follow the verification
URL printed to the example app's stdout, then `POST /api/auth/login` to
get a JWT.

# regstack

Embeddable user registration, login, and account management for FastAPI/MongoDB
applications. One configurable package replaces hand-rolled auth code across
multiple host apps.

## Status

Alpha — milestones M1 + M2 + M3 + M4 + M5 done.

- **M1:** scaffold, config (env+TOML), JWT (per-purpose derived secrets,
  per-token blacklist + bulk revoke), Argon2 password hashing, console
  email, `register/login/logout/me`, `regstack init` wizard.
- **M2:** durable `pending_registrations` (hashed tokens, TTL),
  `verify` + `resend-verification`, `forgot-password` + `reset-password`
  (with bulk revoke), login lockout (HTTP 429 + Retry-After), SMTP backend
  (aiosmtplib), SES backend (lazy aioboto3), Jinja2 email templates with
  host-overridable directories.
- **M3:** account management (`PATCH /me`, `change-password`,
  `change-email` + `confirm-email-change`, `DELETE /account`), JSON admin
  router (stats, list/get/patch/delete users, admin-resend-verification)
  behind `enable_admin_router`, `regstack create-admin` and
  `regstack doctor` CLI commands.
- **M4:** SSR `ui_router` (login, register, verify, forgot, reset,
  confirm-email-change, account dashboard) behind `enable_ui_router`,
  bundled `core.css` + `theme.css` with CSS-custom-property theming,
  bundled `regstack.js` driving form submissions via the JSON API.
  Hosts override visuals by serving their own `theme.css` and pointing
  `config.theme_css_url` at it; full template overrides via
  `regstack.add_template_dir(path)`.
- **M5:** SMS abstraction (`null` / `sns` / `twilio` backends, lazy
  optional installs), phone setup + disable routes, two-step MFA login
  (`mfa_required` response + `/login/mfa-confirm`), SSR `/mfa-confirm`
  page, dashboard "SMS two-factor authentication" section. Behind
  `enable_sms_2fa`. Codes are 6-digit, SHA-256 hashed, TTL'd, with
  attempt tracking.

## Quick start

```bash
uv sync --extra dev
uv run regstack init                  # interactive wizard, writes regstack.toml + regstack.secrets.env
cd examples/minimal
uv run uvicorn main:app --reload
```

Then in another terminal:

```bash
curl -X POST http://localhost:8000/api/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"a@b.test","password":"hunter2hunter2","full_name":"A B"}'

curl -X POST http://localhost:8000/api/auth/login \
    -H 'content-type: application/json' \
    -d '{"email":"a@b.test","password":"hunter2hunter2"}'

# copy access_token from the response
curl http://localhost:8000/api/auth/me -H 'authorization: Bearer <token>'
curl -X POST http://localhost:8000/api/auth/logout -H 'authorization: Bearer <token>'
```

## Embedding

```python
from fastapi import FastAPI
from pymongo import AsyncMongoClient
from regstack import RegStack, RegStackConfig

config = RegStackConfig.load()
mongo = AsyncMongoClient(config.mongodb_url.get_secret_value())
db = mongo[config.mongodb_database]

regstack = RegStack(config=config, db=db)

app = FastAPI()

@app.on_event("startup")
async def _startup() -> None:
    await regstack.install_indexes()

app.include_router(regstack.router, prefix="/api/auth")
```

## Development

```bash
uv sync --extra dev
uv run python -m invoke test          # parallel pytest suite
uv run python -m invoke lint          # ruff + mypy
uv run python -m invoke run-example   # boot examples/minimal
```

A local MongoDB on `mongodb://localhost:27017` is required for the integration
tests. Each xdist worker creates and tears down its own database.

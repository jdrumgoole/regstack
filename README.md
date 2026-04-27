# regstack

[![CI](https://github.com/jdrumgoole/regstack/actions/workflows/test.yml/badge.svg)](https://github.com/jdrumgoole/regstack/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/built%20with-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](https://github.com/jdrumgoole/regstack/blob/main/LICENSE)

**Drop-in user accounts for FastAPI + MongoDB.** Stop hand-rolling
register / login / verify / reset / 2FA in every project — install
regstack, point it at your MongoDB, and you're done.

📚 **Docs:** <https://regstack.readthedocs.io>
&nbsp;·&nbsp;
🧪 **Try it:** [`examples/minimal`](https://github.com/jdrumgoole/regstack/tree/main/examples/minimal)
&nbsp;·&nbsp;
🛡️ **Security model:** [security guide](https://regstack.readthedocs.io/en/latest/security.html)

---

## What you get

```
✔ Email + password registration with verification
✔ JWT login with per-token revocation AND bulk revocation
✔ Forgot / reset password (anti-enumeration)
✔ Change password / change email / delete account
✔ Optional SMS two-factor authentication
✔ Server-side login lockout (HTTP 429 + Retry-After)
✔ Admin endpoints (list / disable / delete users, stats)
✔ Server-rendered HTML UI you can theme with one CSS file
✔ Pluggable email (console / SMTP / SES) and SMS (null / SNS / Twilio)
✔ Argon2 password hashing, CSP-friendly templates, anti-enumeration
✔ Setup wizard (`regstack init`) and health-check (`regstack doctor`)
```

Every feature is opt-in. Mount only the JSON router for a headless
backend; flip `enable_ui_router` to also get the bundled SSR pages.

## Why regstack?

Most FastAPI auth tutorials stop at "here's a `/login` route that
returns a JWT" and leave you to assemble the other 30 things real
applications need: email verification, password resets, account
recovery, admin tooling, brute-force protection, MFA, themed pages,
secure storage of one-time tokens, anti-enumeration, bulk session
revocation when a password changes…

regstack ships **all** of that as one Apache-licensed package, with a
test suite that runs in parallel against a real MongoDB and a live demo
you can `curl` end-to-end in two minutes.

## Try it in 30 seconds

```bash
git clone https://github.com/jdrumgoole/regstack && cd regstack
uv sync --extra dev

# minimal config
export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
export REGSTACK_MONGODB_URL=mongodb://localhost:27017

uv run uvicorn examples.minimal.main:app --reload
```

Then visit <http://localhost:8000/account/login> in your browser, or:

```bash
curl -X POST http://localhost:8000/api/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"alice@example.com","password":"hunter2hunter2","full_name":"Alice"}'
```

The bundled example serves a themed SSR dashboard at `/account/me`,
prints verification / reset links and SMS codes to stdout, and
demonstrates how a host overrides regstack's default theme by serving
its own `theme.css` from `examples/minimal/branding/`.

## Embed in your own app

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from regstack import RegStack, RegStackConfig
from regstack.db.client import make_client


config  = RegStackConfig.load()
mongo   = make_client(config)
db      = mongo[config.mongodb_database]
regstack = RegStack(config=config, db=db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await regstack.install_indexes()
    yield
    await mongo.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(regstack.router,   prefix=config.api_prefix)
app.include_router(regstack.ui_router, prefix=config.ui_prefix)   # optional
app.mount(config.static_prefix, regstack.static_files)            # optional
```

That's the whole integration. Configure the rest with `regstack.toml`
or environment variables — see the [configuration
reference](https://regstack.readthedocs.io/en/latest/configuration.html).

## Documentation

| Page | What's there |
|---|---|
| [Quickstart](https://regstack.readthedocs.io/en/latest/quickstart.html) | Install, wizard, minimal embed |
| [Configuration](https://regstack.readthedocs.io/en/latest/configuration.html) | Every `RegStackConfig` field, env vars, TOML layout |
| [Architecture](https://regstack.readthedocs.io/en/latest/architecture.html) | Façade, repos, hooks, lifecycle |
| [Security model](https://regstack.readthedocs.io/en/latest/security.html) | Threat model, JWT scheme, anti-enumeration, MFA |
| [Embedding](https://regstack.readthedocs.io/en/latest/embedding.html) | Custom backends, hooks, multi-tenant |
| [Theming](https://regstack.readthedocs.io/en/latest/theming.html) | CSS variables, template overrides |
| [CLI](https://regstack.readthedocs.io/en/latest/cli.html) | `init`, `create-admin`, `doctor` |
| [API reference](https://regstack.readthedocs.io/en/latest/api.html) | Public types, generated from source |

The same docs are also browsable as Markdown in [`docs/`](https://github.com/jdrumgoole/regstack/tree/main/docs).

## Status

Alpha. Milestones M1 through M6 are complete and verified end-to-end
in the bundled example. See the [changelog](https://regstack.readthedocs.io/en/latest/changelog.html) for the
per-milestone breakdown. The next tagged release will be `v0.1.0`.

## Contributing

Issues and pull requests welcome at
<https://github.com/jdrumgoole/regstack>. Before opening a PR, please
run the test suite and the linter — both should be green:

```bash
uv sync --extra dev
uv run python -m invoke test    # parallel pytest, needs local MongoDB
uv run python -m invoke lint    # ruff + format check + mypy
```

A local MongoDB on `mongodb://localhost:27017` is required for the
integration tests. Each pytest-xdist worker creates and drops its own
database, so the suite is safe to re-run while you iterate.

Security disclosures: see [SECURITY.md](https://github.com/jdrumgoole/regstack/blob/main/SECURITY.md).

## License

[Apache License 2.0](https://github.com/jdrumgoole/regstack/blob/main/LICENSE) © 2026 Joe Drumgoole.

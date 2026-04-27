# regstack

[![CI](https://github.com/jdrumgoole/regstack/actions/workflows/test.yml/badge.svg)](https://github.com/jdrumgoole/regstack/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/built%20with-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](https://github.com/jdrumgoole/regstack/blob/main/LICENSE)

**Production-grade user accounts for your [FastAPI](https://fastapi.tiangolo.com/)
app — without the vendor lock-in, the second service to run, or the homegrown
auth bugs.**

`pip install regstack`, point it at SQLite (default), [PostgreSQL](https://www.postgresql.org/),
or [MongoDB](https://www.mongodb.com/), and you have register / login /
verify-email / reset-password / change-email / delete-account / optional SMS
two-factor / admin endpoints / themable HTML pages — all behind a small Python
API and one config file.

📚 **Docs:** <https://regstack.readthedocs.io>
&nbsp;·&nbsp;
🧪 **Try it:** [`examples/sqlite`](https://github.com/jdrumgoole/regstack/tree/main/examples/sqlite)
&nbsp;·&nbsp;
🛡️ **Security model:** [security guide](https://regstack.readthedocs.io/en/latest/security.html)

---

## The problem regstack solves

Every web application that has users eventually needs the same dozen
endpoints: register, log in, log out, verify email, reset a forgotten
password, change password, change email, delete account, list users for
the admin panel, lock out brute-force attackers, and ideally a second
factor. Every one of those endpoints has a well-known way to get
subtly wrong:

- **Password hashing.** Use [Argon2](https://en.wikipedia.org/wiki/Argon2)
  (the [PHC winner](https://www.password-hashing.net/)), not MD5, SHA-1,
  bcrypt-without-pepper, or — somehow still common — plain text.
- **Token revocation.** A [JWT](https://datatracker.ietf.org/doc/html/rfc7519)
  is signed and self-contained: the server can't "log it out" unless you
  build a revocation list. Forget this and a stolen token works until it
  expires.
- **Account enumeration.** A login or password-reset endpoint that
  responds differently for "user exists" vs "user doesn't" lets an
  attacker harvest your customer list. See
  [OWASP WSTG-IDNT-04](https://owasp.org/www-project-web-security-testing-guide/v42/4-Web_Application_Security_Testing/03-Identity_Management_Testing/04-Testing_for_Account_Enumeration_and_Guessable_User_Account).
- **Bulk session invalidation.** When a user changes their password
  because they think they were compromised, every existing token they
  hold should stop working immediately. Most homegrown JWT layers don't
  do this.
- **One-time tokens.** Verification and password-reset tokens should be
  random, hashed at rest, single-use, and expire fast. Storing the raw
  token in the database is a "now your DB backup is also a credential
  dump" mistake.
- **Phone numbers.** SMS codes need [E.164](https://en.wikipedia.org/wiki/E.164)-validated
  numbers, attempt limits, and an upstream provider. Wiring all of that
  yourself for a single feature is rarely worth it.

Doing all of these correctly, with tests, is two to four weeks of
engineering for a competent team. Doing them once and embedding the
result everywhere is what regstack is for.

## What you get

```
✔ Email + password registration with email verification
✔ JWT login (RFC 7519) with per-token revoke AND bulk revoke
✔ Forgot / reset password — anti-enumeration: identical responses
✔ Change password (revokes old tokens) / change email (re-verify)
✔ Delete account
✔ Optional SMS two-factor (TOTP-style 6-digit codes over SMS)
✔ Server-side login lockout (HTTP 429 + Retry-After)
✔ Admin endpoints (list / disable / delete users, stats)
✔ Server-rendered HTML pages, theme with one CSS file
✔ Pluggable email (console / SMTP / Amazon SES) and SMS (Amazon SNS / Twilio)
✔ Argon2 password hashing, CSP-friendly templates
✔ Setup wizard (`regstack init`) and config validator (`regstack doctor`)
✔ Three storage backends: SQLite, PostgreSQL, MongoDB — chosen by URL
```

Every feature is opt-in. Mount only the JSON router for a headless
backend; flip `enable_ui_router` to also get the bundled SSR pages.
Skip the SMS extras and you don't pull `twilio` or `aioboto3`.

## Why not just use…?

There are real alternatives. Here's why regstack might still be the
right call.

| Alternative | Why you might pick it | Why you might pick regstack instead |
|---|---|---|
| **[Auth0](https://auth0.com/) / [Clerk](https://clerk.com/) / [WorkOS](https://workos.com/) / [Stytch](https://stytch.com/)** (hosted SaaS) | Zero ops. Polished UI. Enterprise SSO out of the box. | Cost scales per-user. Your auth lives on someone else's servers. Your customer list is in their database. Vendor lock-in is real and migrations are painful. |
| **[Keycloak](https://www.keycloak.org/) / [Authentik](https://goauthentik.io/) / [Authelia](https://www.authelia.com/) / [Ory Kratos](https://www.ory.sh/kratos/)** (self-hosted IAM) | Full identity platform. SAML, OIDC, federation. | A separate Java/Go service to run, monitor, back up, upgrade, and reason about. Heavyweight for "let users sign up". Schema lives outside your app. |
| **[fastapi-users](https://fastapi-users.github.io/fastapi-users/)** | Same language, same framework. Good registration / login primitives. | Doesn't ship verification flows, anti-enumeration, bulk revoke, SMS MFA, admin endpoints, or themable pages — you build those. regstack is the longer tail. |
| **Roll your own** | Total control. No dependency to learn. | You re-solve every bullet from "The problem" above, including the ones you didn't know existed yet. Two to four engineering weeks, then forever to maintain. |

regstack's bet is that for most FastAPI apps the right answer is
**embed a small Python library that owns the boring 80% correctly, and
keep the user table in your own database** — not "stand up a separate
auth product" and not "write the boring 80% from scratch each time".

## 30-second start

```bash
git clone https://github.com/jdrumgoole/regstack && cd regstack
uv sync --extra dev

# Generate a JWT signing secret. SQLite is the default backend, no DB to install.
export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')

uv run uvicorn examples.sqlite.main:app --reload
```

Then visit <http://localhost:8000/account/login> in your browser, or
register from the command line:

```bash
curl -X POST http://localhost:8000/api/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"alice@example.com","password":"hunter2hunter2","full_name":"Alice"}'
```

The bundled example serves themed SSR pages at `/account/*`, prints
verification / reset links and SMS codes to stdout (the `console`
email/SMS backends), and shows how a host overrides regstack's default
look by serving its own `theme.css`.

Want PostgreSQL or MongoDB instead? Set `REGSTACK_DATABASE_URL` to a
`postgresql+asyncpg://...` or `mongodb://...` URL and install the matching
extra (`uv sync --extra postgres` or `uv sync --extra mongo`). The
schema is created on first boot.

## Embed in your own app

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from regstack import RegStack, RegStackConfig


config = RegStackConfig.load()             # env vars + regstack.toml
regstack = RegStack(config=config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await regstack.install_schema()        # idempotent: runs migrations / creates indexes
    yield
    await regstack.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(regstack.router,    prefix=config.api_prefix)
app.include_router(regstack.ui_router, prefix=config.ui_prefix)   # optional
app.mount(config.static_prefix, regstack.static_files)            # optional
```

That is the whole integration. The rest of the surface area —
extending the user model, registering hooks (`user_registered`,
`password_reset`, …), supplying your own email service — is in the
[embedding guide](https://regstack.readthedocs.io/en/latest/embedding.html).

## Documentation

| Page | What's there |
|---|---|
| [Quickstart](https://regstack.readthedocs.io/en/latest/quickstart.html) | Install, wizard, minimal embed |
| [Configuration](https://regstack.readthedocs.io/en/latest/configuration.html) | Every `RegStackConfig` field, env vars, TOML layout |
| [Architecture](https://regstack.readthedocs.io/en/latest/architecture.html) | Façade, backends, repos, hooks, lifecycle |
| [Security model](https://regstack.readthedocs.io/en/latest/security.html) | Threat model, JWT scheme, anti-enumeration, MFA |
| [Embedding](https://regstack.readthedocs.io/en/latest/embedding.html) | Custom backends, hooks, multi-tenant |
| [Theming](https://regstack.readthedocs.io/en/latest/theming.html) | CSS variables, template overrides |
| [CLI](https://regstack.readthedocs.io/en/latest/cli.html) | `init`, `create-admin`, `doctor` |
| [API reference](https://regstack.readthedocs.io/en/latest/api.html) | Public types, generated from source |

The same docs are also browsable as Markdown in [`docs/`](https://github.com/jdrumgoole/regstack/tree/main/docs).

## Status

Alpha. Single-file SQLite is the default and runs with no infrastructure;
PostgreSQL and MongoDB backends pass the same parametrized integration
suite. The next tagged release is `v0.2.0`. See the
[changelog](https://regstack.readthedocs.io/en/latest/changelog.html)
for the per-milestone breakdown.

## Contributing

Issues and pull requests welcome at
<https://github.com/jdrumgoole/regstack>. Before opening a PR, please
run the test suite and the linter — both should be green:

```bash
uv sync --extra dev
uv run python -m invoke test-all   # SQLite + Mongo + Postgres in parallel
uv run python -m invoke lint       # ruff + format check + mypy
```

`invoke test-sqlite` is the fast inner-loop variant that needs no
database services. `invoke test-all` is what CI runs and what gates a
release. Each pytest-xdist worker isolates its own database, so the
full suite is safe to re-run while you iterate.

Security disclosures: see [SECURITY.md](https://github.com/jdrumgoole/regstack/blob/main/SECURITY.md).

## License

[Apache License 2.0](https://github.com/jdrumgoole/regstack/blob/main/LICENSE) © 2026 Joe Drumgoole.

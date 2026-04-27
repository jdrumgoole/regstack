# regstack

Embeddable user registration, login, and account management for FastAPI
apps. SQLite by default (zero infrastructure); Postgres and MongoDB
backends ship in the same package, switched by URL scheme.

```{toctree}
:maxdepth: 2
:caption: Getting started

quickstart
configuration
```

```{toctree}
:maxdepth: 2
:caption: Guides

architecture
security
embedding
theming
cli
```

```{toctree}
:maxdepth: 2
:caption: Reference

api
changelog
```

## What's in the box

- **Three backends, one API**: SQLite (default), Postgres
  (asyncpg), MongoDB (pymongo). Same routers, same hooks; switch by
  changing the `database_url`.
- **JSON API**: register, verify (resend), login (with optional SMS
  second step), logout, `me`, change-password, change-email + confirm,
  forgot/reset-password, delete-account, admin endpoints.
- **Server-rendered UI** (opt-in): login, register, verify, forgot,
  reset, mfa-confirm, account dashboard. Themed via CSS custom
  properties; full template overrides per host.
- **CLIs**: `regstack init` (interactive setup wizard), `regstack
  create-admin`, `regstack doctor`.
- **Pluggable email + SMS**: email (`console` / SMTP / SES), SMS
  (`null` / SNS / Twilio).
- **Security**: Argon2 password hashing, per-purpose JWT signing keys,
  per-token revocation + bulk revocation, login lockout, durable
  email-verification storage with hashed tokens, 6-digit SMS codes
  with attempt limits, anti-enumeration on forgot/resend endpoints,
  CSP-friendly templates.

## Project status

Alpha. Multi-backend support landed in 0.2.0; demos for each backend
live under `examples/{sqlite,postgres,mongo}/`.

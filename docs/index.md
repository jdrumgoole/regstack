# regstack

regstack is a Python library that gives a
[FastAPI](https://fastapi.tiangolo.com/) application a complete user
account system — registration, login, password reset, email
verification, optional SMS two-factor, and admin endpoints — without
making you build any of it yourself.

It runs against SQLite by default (no database server to install),
and switches to [PostgreSQL](https://www.postgresql.org/) or
[MongoDB](https://www.mongodb.com/) by changing one URL in your config.
The same routers and the same hooks work against all three.

## Why does this exist?

Building user accounts well is harder than it looks. Even small apps
need to choose a password hashing scheme, implement
[JWT](https://datatracker.ietf.org/doc/html/rfc7519) revocation (so
logouts and password changes actually invalidate tokens), defend
against account enumeration on `/forgot-password`, store one-time
verification tokens hashed at rest, and lock out brute-force attackers
without telling them they're locked out.

regstack does that work once, in one Apache-licensed package, so your
app can focus on whatever it actually does for users.

The full case for a shared library — and the comparison with hosted
auth (Auth0, Clerk, WorkOS, Stytch), self-hosted IAM (Keycloak,
Authentik, Authelia, Ory Kratos), and lower-level libraries
(`fastapi-users`) — is in the [README](https://github.com/jdrumgoole/regstack#why-not-just-use).

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

- **Three storage backends, one API.** SQLite (the default — single
  file, no server), Postgres (via asyncpg), MongoDB (via pymongo).
  Same routers, same hooks; switch by changing the `database_url`.
- **JSON API.** Register, verify email, resend verification, log in
  (with optional SMS second step), log out, `me`, change password,
  change email + confirm, forgot/reset password, delete account, admin
  endpoints.
- **Server-rendered HTML pages** (opt-in). Login, register, verify,
  forgot, reset, MFA confirm, account dashboard. Themed via CSS custom
  properties — no template editing required for a re-skin. Full
  template overrides are still possible per host.
- **CLIs.** `regstack init` (interactive setup wizard),
  `regstack create-admin`, `regstack doctor`.
- **Pluggable email and SMS.** Email backends: `console` (dev), SMTP,
  [Amazon SES](https://aws.amazon.com/ses/). SMS backends:
  [Amazon SNS](https://aws.amazon.com/sns/),
  [Twilio](https://www.twilio.com/). Plug your own in by implementing
  one method.
- **Security defaults you would otherwise have to research.** Argon2id
  password hashing, per-purpose [JWT](https://datatracker.ietf.org/doc/html/rfc7519)
  signing keys, per-token revocation, bulk session invalidation on
  password change, login lockout with HTTP 429 + `Retry-After`,
  durable hashed verification tokens, 6-digit SMS codes with attempt
  limits, anti-enumeration on forgot/resend endpoints, CSP-friendly
  templates with no inline styles.

## Project status

Alpha. Multi-backend support landed in 0.2.0; demos for each backend
live under `examples/{sqlite,postgres,mongo}/`. The integration suite
runs every test against every active backend in parallel, so a green
CI on `main` is a strong correctness signal.

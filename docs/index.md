# regstack

**Production-grade user accounts for your [FastAPI](https://fastapi.tiangolo.com/)
app — without the vendor lock-in, the second service to run, or the
homegrown auth bugs.**

`pip install regstack`, point it at SQLite (default),
[PostgreSQL](https://www.postgresql.org/), or
[MongoDB](https://www.mongodb.com/), and you have register / login /
verify-email / reset-password / change-email / delete-account /
optional SMS two-factor / admin endpoints / themeable HTML pages —
all behind a small Python API and one config file.

## The problem regstack solves

Every web application that has users eventually needs the same dozen
endpoints: register, log in, log out, verify email, reset a forgotten
password, change password, change email, delete account, list users
for the admin panel, lock out brute-force attackers, and ideally a
second factor. Every one of those endpoints has a well-known way to
get subtly wrong:

- **Password hashing.** Use Argon2id, not MD5, SHA-1,
  bcrypt-without-pepper, or — somehow still common — plain text.
- **Token revocation.** A [JWT](https://datatracker.ietf.org/doc/html/rfc7519)
  is signed and self-contained: the server can't "log it out" unless
  you build a revocation list. Forget this and a stolen token works
  until it expires.
- **Account enumeration.** A login or password-reset endpoint that
  responds differently for "user exists" vs "user doesn't" lets an
  attacker harvest your customer list.
- **Bulk session invalidation.** When a user changes their password
  because they think they were compromised, every existing token they
  hold should stop working immediately. Most homegrown JWT layers
  don't do this.
- **One-time tokens.** Verification and password-reset tokens should
  be random, hashed at rest, single-use, and expire fast. Storing the
  raw token in the database is a "now your DB backup is also a
  credential dump" mistake.
- **Phone numbers.** SMS codes need E.164-validated numbers, attempt
  limits, and an upstream provider. Wiring all of that yourself for a
  single feature is rarely worth it.

Doing all of these correctly, with tests, is two to four weeks of
engineering for a competent team. Doing them once and embedding the
result everywhere is what regstack is for.

## Why not just use…?

There are real alternatives. Here's why regstack might still be the
right call.

| Alternative | Why you might pick it | Why you might pick regstack instead |
|---|---|---|
| **[Auth0](https://auth0.com/) / [Clerk](https://clerk.com/) / [WorkOS](https://workos.com/) / [Stytch](https://stytch.com/)** (hosted SaaS) | Zero ops. Polished UI. Enterprise SSO out of the box. | Cost scales per-user. Your auth lives on someone else's servers. Your customer list is in their database. Vendor lock-in is real and migrations are painful. |
| **[Keycloak](https://www.keycloak.org/) / [Authentik](https://goauthentik.io/) / [Authelia](https://www.authelia.com/) / [Ory Kratos](https://www.ory.sh/kratos/)** (self-hosted IAM) | Full identity platform. SAML, OIDC, federation. | A separate Java/Go service to run, monitor, back up, upgrade, and reason about. Heavyweight for "let users sign up". Schema lives outside your app. |
| **[fastapi-users](https://fastapi-users.github.io/fastapi-users/)** | Same language, same framework. Good registration / login primitives. | Doesn't ship verification flows, anti-enumeration, bulk revoke, SMS MFA, admin endpoints, or themeable pages — you build those. regstack is the longer tail. |
| **Roll your own** | Total control. No dependency to learn. | You re-solve every bullet from "The problem" above, including the ones you didn't know existed yet. Two to four engineering weeks, then forever to maintain. |

regstack's bet is that for most FastAPI apps the right answer is
**embed a small Python library that owns the boring 80% correctly,
and keep the user table in your own database** — not "stand up a
separate auth product" and not "write the boring 80% from scratch
each time".

## About the examples

To keep URLs and config values consistent across the docs, every
example pretends to embed regstack into a fictional app called
**Acme Wine Cellar** — a small SaaS that helps people track what's
in their cellar. Throughout the docs:

| What | Value |
|---|---|
| Public host | `cellar.example.com` |
| `base_url` | `https://cellar.example.com` |
| Database host (prod) | `db.cellar.example.com` |
| Database user | `acme` |
| Database password | `hunter2hunter2` |
| Database name | `cellar` |
| Email sender | `noreply@cellar.example.com` |
| Local dev port | `localhost:8000` |

So a Postgres URL looks like
`postgresql+asyncpg://acme:hunter2hunter2@db.cellar.example.com:5432/cellar`,
a MongoDB URL like
`mongodb://acme:hunter2hunter2@db.cellar.example.com:27017/cellar`,
and the local SQLite path `sqlite+aiosqlite:///./cellar.db`. Substitute
your own values when copying — the shape is the only thing that
matters.

## What's in the box

- **Three storage backends, one API.** SQLite (the default — single
  file, no server), Postgres (via asyncpg), MongoDB (via pymongo).
  Same routers, same hooks; switch by changing the `database_url`.
- **JSON API.** Register, verify email, resend verification, log in
  (with optional SMS second step), log out, `me`, change password,
  change email + confirm, forgot/reset password, delete account,
  admin endpoints.
- **Server-rendered HTML pages** (opt-in). Login, register, verify,
  forgot, reset, MFA confirm, account dashboard. Themed via CSS
  custom properties — no template editing required for a re-skin.
  Full template overrides are still possible per host.
- **CLIs.** `regstack init` (interactive setup wizard),
  `regstack oauth setup` (guided Google OAuth client configuration in
  a native webview window), `regstack create-admin`, `regstack doctor`.
- **OAuth — Sign in with Google** (opt-in, since 0.3.0). Authorization
  Code with PKCE, ID-token verification, identity-linking with a
  default-refuse policy hosts can opt out of. Connected-accounts
  panel on the SSR `/account/me` page. See [the OAuth guide](oauth.md).
- **Pluggable email and SMS.** Email backends: `console` (dev), SMTP,
  [Amazon SES](https://aws.amazon.com/ses/). SMS backends:
  [Amazon SNS](https://aws.amazon.com/sns/),
  [Twilio](https://www.twilio.com/). Plug your own in by implementing
  one method.
- **Security defaults you would otherwise have to research.**
  Argon2id password hashing, per-purpose
  [JWT](https://datatracker.ietf.org/doc/html/rfc7519) signing keys,
  per-token revocation, bulk session invalidation on password change,
  login lockout with HTTP 429 + `Retry-After`, durable hashed
  verification tokens, 6-digit SMS codes with attempt limits,
  anti-enumeration on forgot/resend endpoints, CSP-friendly templates
  with no inline styles.

## Where to next

- New here? Start with the [Quickstart](quickstart.md) — install,
  generate a config, register a user end-to-end.
- Embedding regstack in an existing app? Read
  [Embedding](embedding.md) for the patterns most hosts adopt
  (custom email, hooks, multiple regstacks, theming).
- Curious how it's put together internally? See
  [Architecture](architecture.md).
- Designing a deployment? The [Security model](security.md) page is
  the threat model, the JWT scheme, and the things you still own as
  a host.

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
oauth
theming
cli
```

```{toctree}
:maxdepth: 2
:caption: Reference

api
changelog
```

## Project status

Alpha. Latest tagged release: `v0.4.0`. SQLite is the default and
runs with no infrastructure; PostgreSQL and MongoDB pass the same
parametrized integration suite. The full backend matrix runs in
parallel against every test, so a green CI on `main` is a strong
correctness signal.

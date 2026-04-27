# regstack

Embeddable user registration, login, and account management for FastAPI /
MongoDB apps. One configurable Python package replaces hand-rolled auth
code across multiple host applications.

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

- **JSON API**: register, verify (resend), login (with optional SMS
  second step), logout, `me`, change-password, change-email + confirm,
  forgot/reset-password, delete-account, admin endpoints.
- **Server-rendered UI** (opt-in): login, register, verify, forgot,
  reset, mfa-confirm, account dashboard. Themed via CSS custom
  properties; full template overrides per host.
- **CLIs**: `regstack init` (interactive setup wizard), `regstack
  create-admin`, `regstack doctor`.
- **Pluggable backends**: email (`console` / SMTP / SES), SMS
  (`null` / SNS / Twilio).
- **Security**: Argon2 password hashing, per-purpose JWT signing keys,
  per-token revocation + bulk revocation, login lockout, durable
  email-verification storage with hashed tokens, 6-digit SMS codes
  with attempt limits, anti-enumeration on forgot/resend endpoints,
  CSP-friendly templates.

## Project status

Alpha. Milestones M1 through M5 are complete and verified end-to-end
in `examples/minimal/`.

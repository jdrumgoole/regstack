# Changelog

All notable changes to this project are documented here. The
authoritative copy lives at
[`docs/changelog.md`](docs/changelog.md) and is rendered into the
Sphinx docs.

## 0.3.0 — 2026-04-30

**OAuth — Sign in with Google.** Opt-in via the new `oauth` extra
and `enable_oauth=True`. Five JSON endpoints, an SSR
`/account/oauth-complete` page, "Sign in with Google" button on the
login page, and a Connected-accounts panel on `/account/me`.

Schema migration `0002_oauth.py` creates `oauth_identities` +
`oauth_states` and makes `users.hashed_password` nullable
(OAuth-only users have no password). Roll forward via
`regstack migrate` or first-boot `install_schema()` — no manual
intervention.

Account-linking policy defaults to **refuse**: if a Google sign-in
arrives carrying an email that already belongs to a password-
registered user, the callback returns `?error=email_in_use` and the
user must sign in then explicitly link from `/account/me`. Hosts
that consciously accept the email-recycling threat for UX can flip
`oauth.auto_link_verified_emails = true`. See
[`docs/oauth.md`](https://regstack.readthedocs.io/en/latest/oauth.html)
and [`tasks/oauth-design.md`](https://github.com/jdrumgoole/regstack/blob/main/tasks/oauth-design.md)
for the full threat model.

**Migration**

- Install the new extra: `uv add 'regstack[oauth]'`.
- Set `enable_oauth = true` and provide `oauth.google_client_id` +
  `oauth.google_client_secret`.
- Run `regstack migrate` (SQL backends only) or rely on
  `install_schema()` at first boot.

`BaseUser.hashed_password` is now `str | None`. Code that imported
the field type explicitly will need to widen it.

## 0.2.6 — 2026-04-28

Bug fix.

- **Fix:** `/admin/stats` reported `pending_registrations: 0` on
  every SQL backend. The route reached into the Mongo repo's private
  `_collection` attribute and silently fell back to `0` when the
  attribute was absent. Added `count_unexpired(now=None)` to
  `PendingRepoProtocol` with Mongo + SQL implementations and routed
  through `rs.clock.now()` so the count respects the injected clock.
  New parametrized integration test exercises the count on every
  backend.

## 0.2.5 — 2026-04-28

Bug fix + tooling.

- **Fix:** `regstack doctor` against a SQL backend crashed with
  `asyncio.run() cannot be called from a running event loop`. The
  schema check called `regstack.backends.sql.migrations.current()`,
  which used `asyncio.run()` internally — invalid inside doctor's own
  `asyncio.run`. Added `current_async()` and switched the doctor
  command to use it. Sync `current()` is preserved for the migrate
  CLI.
- **New:** `inv coverage [--no-html] [--fail-under=N]` runs the full
  three-backend matrix under coverage and writes term + HTML reports.
  Branch coverage is on by default.
- Test coverage uplift on the CLI: `cli/init.py` 14% → 88%,
  `cli/doctor.py` 61% → 87%. Total: **85% → 87.1%**.

## 0.2.4 — 2026-04-28

**Breaking** — back-compat shims removed:

- `RegStack.install_indexes()` (alias for `install_schema()`).
- `ObjectIdStr` alias for `IdStr` in `regstack.models._objectid`.
- Re-exports of `UserAlreadyExistsError`,
  `PendingAlreadyExistsError`, `MfaVerifyOutcome`, and
  `MfaVerifyResult` from `regstack.backends.mongo.repositories.*`.
  Their canonical home is `regstack.backends.protocols`.

If you import any of these from the old paths, switch to:
- `RegStack.install_schema()`
- `from regstack.models._objectid import IdStr`
- `from regstack.backends.protocols import UserAlreadyExistsError`
  (and friends).

The internal mongo `install_indexes(db, config)` function is unchanged.

## 0.2.3 — 2026-04-28

Docs-only release. Restructured the API reference around the current
package layout (post multi-backend refactor) and added Google-style
docstrings (Args / Returns / Raises) to the public surface — RegStack,
JwtCodec, PasswordHasher, LockoutService, AuthDependencies,
HookRegistry, EmailService, SmsService, the router builders, and the
Clock implementations. Dataclass field docs moved to PEP 258
attribute docstrings. Sphinx builds clean under `-W` again.

## 0.2.2 — 2026-04-28

Docs-only release. The README and Sphinx docs landing page now lead
with the same pitch (problem framing, "Why not just use…?" comparison
vs Auth0 / Clerk / Keycloak / fastapi-users) before diving into
architecture. Hyperlink density trimmed back: only major external
packages, products, and JWT (RFC 7519) are linked — Wikipedia trivia,
MDN basics, OWASP article links, and deep-dependency helper-class
docs were removed.

## 0.2.1 — 2026-04-28

Hotfix for 0.2.0: `import regstack` failed on a base install because
several modules in the import path (`models/_objectid.py`,
`backends/protocols.py`, four routers, and the SQL `mfa_code_repo`)
had unconditional `from bson …` / `from regstack.backends.mongo …`
imports — but `pymongo` became an optional `mongo` extra in 0.2.0.
Added a CI smoketest that builds the wheel and imports it in a
no-extras venv, plus an in-process regression test that blocks `bson`
/ `pymongo` via `sys.meta_path`.

## 0.2.0 — 2026-04-28

Multi-backend support — SQLite (default), Postgres, MongoDB — switched
by `database_url` URL scheme. Bundled Alembic migrations for SQL
backends. Embedding API change: `RegStack(config=, db=)` →
`RegStack(config=, backend=None)`. README + core docs rewritten for
less-expert readers (problem framing, hyperlinks to external
standards, comparison vs Auth0/Clerk/Keycloak/fastapi-users).

See [`docs/changelog.md`](docs/changelog.md) for the full per-feature
breakdown.

## 0.1.1 — 2026-04-27

- Rewrite README relative links as absolute URLs so they resolve on the
  PyPI project page. README-only release.

## 0.1.0 — 2026-04-27

First tagged release. Bundles M1–M6 from the development plan into a
single Apache-2.0 package on PyPI.

See [`docs/changelog.md`](docs/changelog.md) for the per-milestone
breakdown of M1 through M6.

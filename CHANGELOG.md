# Changelog

All notable changes to this project are documented here. The
authoritative copy lives at
[`docs/changelog.md`](docs/changelog.md) and is rendered into the
Sphinx docs.

## 0.5.7 — 2026-05-13

Documentation-only follow-up to 0.5.6.

- `docs/configuration.md` now documents the per-route `*_rate_limit`
  family (added in 0.5.4) instead of pointing at
  `login_max_per_minute` / `login_max_per_hour` as reserved future
  fields.
- `docs/security.md` no longer references
  `PasswordHasher.needs_rehash` (removed in 0.5.6). Replacement
  guidance points hosts at `pwdlib.PasswordHash.verify_and_update`.
- Root `CHANGELOG.md` backfilled with 0.4.0 and 0.5.0 entries so it
  matches `docs/changelog.md`.

## 0.5.6 — 2026-05-13

Eleven days of security-review remediation, supply-chain hardening,
a full `mypy --strict` cleanup pass, and the per-route rate-limits
feature rolled up into a single release.

**Per-route IP rate limits.** Opt-in via the new `rate_limit` extra
(or a host-supplied `slowapi.Limiter`) plus any of the new
`RegStackConfig.*_rate_limit` fields (`login_rate_limit`,
`register_rate_limit`, `forgot_password_rate_limit`,
`reset_password_rate_limit`, `verify_rate_limit`,
`resend_verification_rate_limit`, `change_password_rate_limit`,
`change_email_rate_limit`, `confirm_email_change_rate_limit`,
`delete_account_rate_limit`). Each accepts a slowapi-syntax string
(`"5/minute"`, `"5/minute;20/hour"`). Empty / unset means no limit
on that route — `LockoutService` still defends `/login` against
credential stuffing per-account. When `*_rate_limit` strings are
configured but neither a `rate_limiter=` argument is passed nor
the `rate_limit` extra is installed, `RegStack.router` raises
`RuntimeError` on first access — failing closed beats silently
disabling the protection. Hosts remain responsible for
`app.state.limiter` and `app.add_exception_handler(RateLimitExceeded, ...)`;
slowapi owns the 429 response shape. The previously-reserved
`login_max_per_minute` / `login_max_per_hour` fields are kept for
back-compat but unwired.

**Security fixes.**

- JWT 401 detail now returns a static `"Invalid or expired token."`;
  no longer leaks the pyjwt failure reason (signature mismatch /
  expired / malformed / audience mismatch).
- OAuth sign-in now honours `allow_registration=False`. Previously,
  `/register` respected the flag but the OAuth `_resolve_user`
  "brand-new account" branch did not, creating accounts even when
  self-service signup was disabled.
- Admin `DELETE /admin/users/{id}` now cascades `oauth_identities`,
  matching the user-initiated `DELETE /account` path. Previously
  left orphan rows that blocked re-registration of the same Google
  subject.
- `POST /phone/start` and `DELETE /phone` now return 400 (not crash
  with HTTP 500) for OAuth-only users who have no `hashed_password`.

**Breaking change — hook contracts.** `mfa_login_started` and
`phone_setup_started` no longer include the raw OTP code in their
kwargs. Hooks are best-effort observability and are the documented
integration surface for analytics / logging / Slack notifications,
so a plaintext OTP in `**kwargs` is a leak waiting to happen.
Hosts that subscribed to either event to take over SMS delivery
should migrate to a custom `SmsService` subclass — the supported
delivery override.

**Dependency floors raised for CVEs.**

- `pyjwt>=2.12.1` for CVE-2026-32597 (`crit` header bypass, CVSS 7.5).
- `cryptography>=46.0.7` added explicitly to the `oauth` extra for
  CVE-2026-26007 (ECC subgroup attack on the JWKS code path, CVSS
  8.2) plus CVE-2026-34073 and CVE-2026-39892.
- `python-multipart>=0.0.26` for CVE-2026-40347 (DoS via oversized
  multipart preamble).

**Supply chain.** `pypa/gh-action-pypi-publish` in `publish.yml`
pinned to a commit SHA instead of the mutable `release/v1` branch.
The publish job holds `id-token: write`, so a tag/branch swap
upstream would let an attacker push a malicious wheel under our
OIDC identity.

**Removed.** `PasswordHasher.needs_rehash` — called pwdlib's
non-existent `check_needs_rehash` and would `AttributeError` if
anyone invoked it. No callers in src or tests. If you were planning
to use it, call `pwdlib.PasswordHash.verify_and_update` directly.

**Internal.** 72 `mypy --strict` errors cleared across 35 files;
`inv lint` is now green end-to-end. Mongo
`BlacklistRepo.purge_expired` added (protocol parity with SQL).
`KNOWN_EVENTS` reconciled — 7 previously-undeclared events added
(`verification_requested`, `email_change_requested`, `email_changed`,
`phone_setup_started`, `mfa_login_started`, `mfa_enabled`,
`mfa_disabled`). `user_logged_out` now actually fires from
`routers/logout.py` (was listed in `KNOWN_EVENTS` but no router
emitted it).

## 0.5.0 — 2026-05-02

**Theme designer.** `regstack theme design` opens a native pywebview
window with controls for every `--rs-*` CSS custom property and a
real-time preview of the bundled SSR widgets (sign-in form, success /
error banners, danger-zone button). Saving writes `regstack-theme.css`;
the designer round-trips values back into the form on next launch so
iteration is non-destructive. `--print-only` mode takes repeatable
`--var NAME=VALUE` pairs (with a `dark:` prefix for dark-scheme
overrides) and writes the file headlessly. Lives in
`regstack.wizard.theme_designer`; registered as a lazy Click subgroup
so `regstack init` / `doctor` don't pay the pywebview/uvicorn import
cost.

**Docs.** New "About the examples" convention block at the top of
`docs/index.md`. Every URL, email, smtp host, and admin command across
the docs now extrapolates from the same fictional app at
`app.example.com` with `<username>` / `<password>` placeholders.

## 0.4.0 — 2026-05-02

**OAuth setup wizard.** `regstack oauth setup` opens a native webview
window that walks an operator through registering a Google OAuth 2.0
client and merges the credentials into `regstack.toml` +
`regstack.secrets.env` non-destructively (preserves comments, other
tables, unrelated keys). 12-step SPA inside a local-only 127.0.0.1
FastAPI server, gated by a per-launch random token. Each "Next" click
hits a server-side validator so the Write step can never be reached
with bad data. `--print-only` mode skips the GUI for headless / CI
use.

Three new base dependencies — `pywebview>=5.0`, `tomlkit>=0.13`,
`uvicorn[standard]>=0.29` — for the wizard's local server.
`pytest-playwright` added to the `dev` extra; new `inv test-e2e` task
chained into `inv test-all`.

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

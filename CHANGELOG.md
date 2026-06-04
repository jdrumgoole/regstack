# Changelog

All notable changes to this project are documented here. The
authoritative copy lives at
[`docs/changelog.md`](docs/changelog.md) and is rendered into the
Sphinx docs.

## Unreleased

Four findings from the 2026-05-21 / 2026-05-22 daily security reviews.

**Fixed: Google JWKS fetch now has a timeout.** `PyJWKClient` was
constructed without a `timeout`, so the synchronous `urllib` fetch
(offloaded to `asyncio.to_thread`) could pin a worker thread
indefinitely during a Google JWKS outage and, under sustained load,
exhaust the bounded asyncio thread pool. Added a 5-second
`JWKS_FETCH_TIMEOUT_SECONDS`. (Daily security review 2026-05-22 · W-1.)

**Fixed: Google OAuth token-exchange error no longer logs the full
provider response body at WARNING.** On a non-200 token exchange the
provider's response body is now logged at DEBUG; the raised
`OAuthTokenExchangeError` (which the router logs at WARNING) carries only
`HTTP <status>`. (Daily security review 2026-05-22 · I-3.)

**Fixed: `/oauth/exchange` now reports `was_new_account` accurately.**
The field was hardcoded `False`; the callback computed whether it created
a brand-new account but had nowhere to persist it. Added
`oauth_states.result_was_new` (migration `0003`) which the callback sets
and the exchange endpoint reads. (Daily security review 2026-05-22 · I-1.)

**Documented: `phone_number` exposure in the admin user listing.**
`docs/security.md` now spells out that `UserPublic.phone_number` is
returned in plaintext on `GET /admin/users` (regulated PII in some
jurisdictions) and that hosts wanting to mask/omit it should wrap the
admin listing in their own response model. (Daily security review
2026-05-21 · I-2.)

## 0.8.0 — 2026-05-19

`regstack ses setup` guided wizard, plus two security fixes from
the 2026-05-18 daily review.

**Added: `regstack ses setup`.** A pywebview wizard for the SES
email backend, mirroring the existing `regstack oauth setup` flow.
Nine steps walk through region selection, credential source
(`profile` / `explicit` / `chain`), sender-domain identity
verification (via SES `GetIdentityVerificationAttributes`),
sandbox detection (via `GetAccount` with `GetSendQuota` heuristic
fallback for IAM-restricted policies), and a live test send.
Non-clobbering tomlkit + secrets.env merge. Headless
`--print-only` mode for CI / scripting. Gated behind the joint
extra: `pip install 'regstack[wizard,ses]'`.

**Fixed: theme-designer preview no longer ships well-known credentials
in the wheel.** `designer.html` had `alice@example.com` /
`hunter2hunter2` as `value=` attributes on its login-form preview;
flipped to `placeholder=` so the wheel doesn't carry well-known
example creds that could be mistaken for real fixtures.
(Daily security review 2026-05-18 · I-1.)

**Fixed: Google OAuth token-exchange error no longer echoes the
response body.** `exchange_code()` previously raised
`OAuthTokenExchangeError(f"... {body!r}")` on the rare 200-without-id_token
edge case. The body can contain a live short-lived `access_token` in
that path, and the OAuth router logs the exception text at WARNING.
Dropped `{body!r}` from the message; regression test in
`tests/unit/test_oauth_google.py` pins that a planted token never
appears in the exception's `str()` or `args`.
(Daily security review 2026-05-18 · I-2.)

## 0.7.0 — 2026-05-17

Two-week sprint that lands the `regstack validate` end-to-end probe,
seven security-review findings, a clutch of host-integration
ergonomic wins (per-link email URL templates, optional auth
dependency, admin `promote_pending`, explicit SES credentials), and
two breaking API trims (`UserPublic._id` → `id`,
`TokenTransport = "bearer"` only).

The headline is `regstack validate` — a new CLI command that drives
a real deployed install through every auth flow (register, verify,
login, logout, password reset, change-email, OAuth start, SMS 2FA)
from a remote operator workstation, scraping one-time tokens out of
the deployment's stdout via a `--log-source` of your choice
(`file:`, `ssh:`, `docker:`, `cmd:`). The companion to `regstack
doctor`: doctor checks the loaded config, validate checks the
running service.

**Breaking.**

- **`UserPublic` JSON key is `id`, not `_id`.** The `alias="_id"`
  on `UserPublic.id` (and the accompanying `populate_by_name=True`)
  is removed. Every endpoint returning a `UserPublic` —
  `POST /api/auth/register`, `GET /api/auth/me`, `PATCH /api/auth/me`,
  and the admin user endpoints — now sends `id` on the wire.
  `BaseUser` (the Mongo-document model) keeps the alias because it
  round-trips to BSON via `to_mongo()`; only the API contract is
  touched. Clients that read `body["_id"]` should switch to
  `body["id"]`. Hosts hand-rolling a `/me` override solely to swap
  the key shape can drop it.
- **`TokenTransport` literal narrowed to `Literal["bearer"]`.**
  `"cookie"` was previously accepted by config validation but
  silently no-op'd (no router ever set `Set-Cookie`). Hosts that
  set `transport = "cookie"` now get a clear pydantic
  `literal_error` at startup instead of a silent
  security-misconfiguration. `RegStackConfig.cookie_domain` is
  removed along with it. `regstack init` no longer offers the
  cookie option either.

**Added.**

- **`regstack validate`.** End-to-end probe of a deployed install
  — registers a throwaway user, walks every auth flow, then
  deletes it. Reads one-time tokens out of the deployment's
  stdout via `--log-source` (file / ssh / docker / arbitrary
  command). Skip phases with `--skip`. Companion to `regstack
  doctor` (which only validates loaded config). See
  `regstack validate --help` for the full operator runbook.
- **`email.log_bodies` and `sms.log_bodies` config flags** to
  promote the console / null backends' body log lines from
  DEBUG → INFO without enabling DEBUG globally. `email.log_bodies`
  defaults to `False`; `sms.log_bodies` defaults to `True`
  (preserves prior null-SMS behaviour). Other backends ignore.
- **`RegStackConfig.email_link_prefix` + auto-resolve from
  `ui_prefix`.** Verification / reset / email-change links now
  default to `<base_url><ui_prefix>/verify?token=...` when the
  bundled UI router is enabled, instead of bare `/verify`. Hosts
  whose SPA owns the auth pages can pin a path explicitly via
  `email_link_prefix`; the bundled UI hosts get the right links
  automatically.
- **`EmailConfig.from_name` defaults to `app_name`** when unset.
  Hosts that change `app_name` to brand outgoing email also get
  the matching `From:` header automatically. Explicit `from_name`
  values still win.
- **Per-link email URL templates.** Three new optional fields on
  `RegStackConfig` — `verify_url_template`,
  `password_reset_url_template`, `email_change_url_template` —
  let SPAs whose router shape doesn't fit
  `/verify?token=...` rewrite the email links. Templates
  substitute `{base_url}` and `{token}` literally. Hash-routed
  SPA: `"{base_url}/#/verify/{token}"`. Sibling subdomain:
  `"https://auth.example.com/verify/{token}"`. Default unset
  falls back to the prefix-based composition above. New helpers
  `RegStackConfig.resolve_{verify,password_reset,email_change}_url(token)`.
- **`current_user_optional` dependency.** Companion to
  `current_user` / `current_admin` on `regstack.deps`. Returns
  `BaseUser | None` instead of raising 401, for endpoints that
  render differently for signed-in vs anonymous callers (cart
  icon, comment-author prefill). Every form of auth failure —
  missing header, wrong scheme, malformed / expired / revoked
  token, deleted or bulk-revoked user — collapses to `None`.
- **`RegStack.promote_pending(email)` + admin route.** Converts
  a `PendingRegistration` row directly into a verified active
  user, bypassing the email-link round-trip. Hashed password and
  full name carry over verbatim. Fires the same `user_verified`
  hook as `POST /verify`. Useful for admin rescue of stuck
  signups, batch seeding from a known-good list, and dev
  fixtures. Exposed as `POST /admin/pending/{email}/promote`
  when the admin router is enabled.
- **Explicit SES credential fields on `EmailConfig`.** New
  `ses_access_key_id` / `ses_secret_access_key` (both `SecretStr |
  None`) let hosts pass AWS creds directly instead of relying on
  boto3's env-var fallthrough. Validated as a pair, mutually
  exclusive with `ses_profile`.

**Security.**

- **CVE-2026-42561 — `python-multipart>=0.0.27`.** Closes a
  network-exploitable DoS via unbounded multipart part-header
  parsing (CVSS 7.5). Previous floor `>=0.0.26` had the earlier
  CVE-2026-40347 fix only.
- **sdist no longer ships internal docs to PyPI.** Added a
  `[tool.hatch.build.targets.sdist]` exclude block. The published
  source tarball used to contain `CLAUDE.md` (with a developer
  home-directory path), the security-review prompt, the full test
  suite, build tooling, and (when built from a worktree) a `.git`
  text file pointing at the operator's worktrees directory.
- **Defensive `ObjectId.is_valid()` on nine Mongo UserRepo
  mutations.** `set_last_login`, `set_tokens_invalidated_after`,
  `update_password`, `set_active`, `set_superuser`, `set_full_name`,
  `set_phone`, `set_mfa_enabled`, and `update_email` now match
  `get_by_id` / `delete`: invalid input no-ops instead of raising
  `bson.errors.InvalidId` (which would have surfaced as a 500 on
  any future caller passing raw external input).
- **Per-IP rate-limit map covers `/login/mfa-confirm` and
  `/oauth/exchange`.** Two new config fields:
  `login_mfa_confirm_rate_limit`, `oauth_exchange_rate_limit`.
  The per-code attempt counter on `mfa_codes` defends each
  individual code; this adds the per-IP layer against distributed
  guessing across many source IPs.
- **OAuth callback `error` query parameter sanitized before
  logging.** A compromised or malicious OAuth provider could
  previously inject newlines / ANSI escapes into the log stream
  via the `error=...` redirect. The callback now strips control
  characters and caps length at 200 before logging.
- **`oauth_states.mode` validated at the MongoDB storage layer.**
  A `$jsonSchema` validator on the collection enforces
  `mode IN ('signin', 'link')`, matching the SQL backend's
  existing `CheckConstraint`. `OAuthState.model_validate()`
  already enforced this at the app layer; this is defence-in-depth.
- **Migration `0002` downgrade refuses to roll back when OAuth-only
  users exist.** The downgrade re-applies `NOT NULL` to
  `users.hashed_password`; if any row has `NULL` (OAuth-only
  signup), it now raises `RuntimeError` with a clear remediation
  message instead of silently succeeding on SQLite (where
  `batch_alter_table`'s CREATE-COPY-DROP-RENAME path skipped
  NOT NULL enforcement).
- **PEP 740 sigstore attestations on the PyPI publish workflow.**
  Each published wheel / sdist is now cryptographically bound to
  the specific GitHub Actions run that produced it, so consumers
  can verify the artefact came from this repo's CI.
- **`workflow_dispatch` removed from `publish.yml`.** Manual runs
  previously uploaded artefacts to Actions storage with no
  version validation, where they could be confused with a real
  release build. Tag-push is the only supported trigger.

**Fixed.**

- **`regstack doctor --send-test-email` honours the new `from_name`
  fall-back.** Before, the probe path passed `config.email.from_name`
  (now `Optional[str]`) straight into `EmailMessage.from_name`
  (typed `str`), producing a `None <addr>` From: header when
  unset.
- **`install_schema()` survives a legacy unnamed unique-on-email
  index.** A host that previously ran
  `db.users.create_index([("email", 1)], unique=True)` from its
  own pre-regstack auth code has a Mongo-auto-named `email_1`
  index. `install_indexes` previously crashed on first boot with
  `IndexOptionsConflict`. It now detects any unnamed/legacy
  unique index over `{"email": 1}`, drops it, and proceeds.
  Idempotent on a healthy DB.
- **`POST /verify` no longer 500s on the admin-promote-meets-user-
  clicks-verify race.** The endpoint now catches
  `UserAlreadyExistsError` from `users.create` and returns a
  graceful 400 ("This email is already registered. Please sign
  in.") instead of letting the unique-constraint violation bubble
  up as a 500.

**Internal.**

- **GitHub Actions pinned ahead of Node 20 deprecation.**
  `actions/checkout` v4→v6.0.2, `astral-sh/setup-uv` v3→v8.1.0,
  `actions/upload-artifact` v4→v7.0.1, `actions/download-artifact`
  v4→v8.0.1. All pins remain commit SHAs.
- **Daily scheduled security-review reports** land under
  `docs/security-reports/` for 2026-05-15 through 2026-05-17.
  The 2026-05-17 report is `[security-clean]`: all warnings from
  the prior two days resolved in this release.

## 0.6.0 — 2026-05-14

**Breaking change for wizard users.** The GUI setup wizards
(`regstack oauth setup`, `regstack theme design`) are now behind a
new optional `wizard` extra. `pip install regstack` no longer pulls
in `pywebview`, `tomlkit`, or `uvicorn[standard]` — three heavy
wizard-only dependencies that every library consumer was paying for,
including a platform browser engine on every fresh install. A
recurring audit recommendation since 0.5.0.

**Migration.**

- If you only embed regstack in a FastAPI app (no `regstack oauth
  setup` or `regstack theme design`): no action needed. The base
  install is now significantly slimmer.
- If you use either setup wizard: install the new extra —
  `pip install 'regstack[wizard]'` or `uv sync --extra wizard`.
  Running a wizard subcommand without the extra now exits with a
  one-line install hint (no ImportError traceback).
- The `dev` extra continues to pull in the wizard deps directly so
  `inv test-all` keeps working without an explicit `--extra wizard`.

Bumped to **0.6.0** (not 0.5.12) because removing top-level deps is
the kind of change that can surprise downstream `pip install
regstack` callers — even though "the GUI wizard CLIs need an extra
now" is the only observable effect.

## 0.5.11 — 2026-05-14

CI / workflow hygiene. No runtime code changes.

- **All third-party GitHub Actions pinned to commit SHAs.**
  `actions/checkout@v4`, `astral-sh/setup-uv@v3`,
  `actions/upload-artifact@v4`, and `actions/download-artifact@v4` now
  use commit SHAs in `.github/workflows/publish.yml` and
  `.github/workflows/test.yml`, with `# v4` / `# v3` trailing comments
  so future operators can resolve and bump. `pypa/gh-action-pypi-publish`
  was already SHA-pinned (#37). A tag swap upstream can no longer
  substitute a malicious version.
- **`permissions:` blocks added to every workflow + job.** Both
  workflows now declare a `permissions: contents: read` default at
  the workflow level and re-state it per job (so a future addition of
  a write-needing action doesn't silently inherit elevated scopes).
  The `publish` job continues to declare `id-token: write` (OIDC
  trusted-publisher exchange) — that's the only scope above
  read-only anywhere in the workflows.
- **`.gitignore` defensive additions.** `.env`, `.env.*`, and the
  common credential-file patterns (`*.pem`, `*.key`, `*.p12`,
  `*.pfx`, `*.jks`, `*.crt`) are now ignored at the repo root. None
  are present today; this is belt-and-braces for misconfigured local
  dev environments. A recurring audit recommendation.

## 0.5.10 — 2026-05-14

Security fixes from the 2026-05-13 / 2026-05-14 daily reviews. All
warnings, no criticals — but several are real exploitable issues.

**Security.**

- **Open-redirect bypass in OAuth `redirect_to`.** `_validate_redirect`
  was forwarding `urlsplit`'s judgment, but browsers normalize values
  like `/\evil.com` and `////evil.com` into the protocol-relative
  `//evil.com` — both of which `urlsplit` reports as same-origin
  paths. The validator now rejects any backslash plus any value that
  doesn't start with a single `/` followed by a non-slash character.
- **CVE-2025-62727 — `fastapi` floor raised to `>=0.120.0`.** Starlette
  DoS via large request bodies after multipart processing.
- **CVE-2025-27516 — `jinja2` floor raised to `>=3.1.6`.** Sandbox
  breakout via the `|attr` filter (only relevant if hosts allow
  user-controlled templates; tightening the floor regardless).
- **Login lockout no longer skips disabled / unverified accounts.**
  `POST /login` now records a failure before raising HTTP 403 for
  `is_active=False` and (when `require_verification=True`)
  `is_verified=False` users. Password verification was also re-ordered
  to run **before** those checks, so an attacker without the password
  can't distinguish disabled vs active accounts by HTTP code alone.
- **`POST /change-email` no longer enumerates registered addresses.**
  An authenticated attacker could previously iterate the email
  namespace via the 409 vs 202 response distinction. The endpoint now
  always returns 202; if the candidate is already registered, no
  confirmation email is sent (the legitimate user finds out by not
  receiving it). Matches the existing anti-enumeration stance on
  `/forgot-password` and `/resend-verification`.
- **Admin resend-verification rejects OAuth-only users.** Previously
  attempted to construct a `PendingRegistration` from a user with
  `hashed_password=None`, which either failed validation or stored
  the literal string `"None"` in the pending row. Now returns 400
  with a clear message.

## 0.5.9 — 2026-05-13

**`OAuthConfig.enforce_mfa_on_oauth_signin` is now wired.** The flag
has been on the config since the OAuth router shipped (0.3.0) and the
wizard surfaced it, but the callback never read it — operators who
flipped it on still got OAuth sign-ins that bypassed the SMS second
factor. The High #1 finding from the post-0.5.6 consistency audit.

Now, when the flag is `true` and the resolved user has SMS MFA set
up (`is_mfa_enabled=True` plus a `phone_number`):

- The OAuth callback sends the SMS code and stashes a short-lived
  `login_mfa` pending JWT in the state row (instead of a session
  token).
- `POST /oauth/exchange` returns `mfa_required=True` and
  `mfa_pending_token=...` (with no `access_token`) so the SPA knows
  to redirect to `/account/mfa-confirm`.
- The SPA's bundled `regstack.js` `oauth-complete` handler stashes
  the pending token under `regstack.mfa_pending` (same key the
  password-login MFA flow uses) and redirects.
- The user enters the SMS code and hits the existing
  `POST /login/mfa-confirm` endpoint — same downstream path as the
  password-login second factor.

Link flows (`mode="link"`) are exempt: the user was already
authenticated when they kicked off the link, so adding SMS friction
on top of an already-authenticated link operation has no
threat-model win.

The `ExchangeResponse` model grew two optional fields
(`mfa_required: bool = False`, `mfa_pending_token: str | None = None`)
and `access_token` is now defaulted to `""` so the MFA branch can
return cleanly. Existing handlers reading `access_token` keep
working — they just need to check `mfa_required` first.

## 0.5.8 — 2026-05-13

Audit-driven consistency cleanup — small fixes across the API surface
flagged by the post-0.5.6 consistency review.

**Security**

- **`oauth.completion_ttl_seconds` is finally enforced.** The flag has
  been on `OAuthConfig` since the OAuth router shipped, but the
  callback never used it: a state row stayed valid for the full
  `state_ttl_seconds` (300s default) between callback completion and
  `/oauth/exchange`. Now `set_result_token(...)` bumps the row's
  expiry down to `now + completion_ttl_seconds` (30s default), so the
  blast radius of a stolen state_id post-callback is the documented
  30-second window. `OAuthStateRepoProtocol.set_result_token` grew an
  optional `new_expires_at=` kwarg to make this atomic with the
  token write.

**Changed (UserPublic surface)**

- `UserPublic` now serialises `updated_at` and
  `tokens_invalidated_after`. SPAs comparing the latter against their
  cached session JWT's `iat` can detect a forced sign-out after a
  password / email change without an extra round-trip.

**Changed (hook payloads)**

- `oauth_signin_started` in `mode="link"` now carries the
  authenticated `user=` kwarg, matching `oauth_signin_completed` and
  `oauth_account_linked`. The `mode="signin"` call site stays without
  `user=` (there isn't one yet — sign-in is what produces it).

**Internal**

- `OAuthConfig.completion_ttl_seconds` config field is now load-bearing
  (was previously declared-but-unread).
- `MessageResponse` in `routers/oauth.py` deleted; the router now uses
  the shared one from `routers/_schemas.py`. OpenAPI no longer carries
  two identically-named schemas.
- `MongoOAuthStateRepo` / `SqlOAuthStateRepo` `set_result_token` grew
  a `new_expires_at` parameter (default `None`, so existing callers
  see no change).
- `MongoBlacklistRepo.purge_expired` switched from `$lte` to `$lt` to
  match the rest of the `purge_expired` family across both backends.
  Edge-instant tokens get one more microsecond of life — the
  bulk-revoke check (which DOES use `<=`) is unchanged.
- Dead `create()` and `delete_by_id()` methods removed from
  `MongoPendingRepo` — neither was in the protocol or the SQL impl,
  and nothing in src or tests called them.
- OAuth `start` and `callback` endpoints now declare
  `response_class=RedirectResponse` and `status_code=302`. OpenAPI
  surfaces the redirect intent properly.
- Custom-claim JWT encoder in `routers/account.py` (email-change
  token) now emits `iat` as a float instead of `int`, matching the
  three other custom-claim encoders and the bulk-revoke contract.
- `routers/verify.py` `created_at` for resent pending registrations
  now goes through `rs.clock.now()` instead of wall-clock
  `datetime.now(UTC)` — keeps `FrozenClock`-driven tests
  deterministic.
- `BaseUser.model_config = ConfigDict(extra="allow")` got a
  comment explaining why it's the only model in the package that
  doesn't `extra="forbid"`.

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

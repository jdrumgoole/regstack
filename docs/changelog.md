# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/) once `1.0.0` ships.

## Unreleased

### Headline

Two ways a secret could quietly leak, closed

A JWT signing secret shorter than thirty-two characters used to be
accepted without comment. HMAC-SHA256 pads a short key with zero bytes
rather than refusing it, so `REGSTACK_JWT_SECRET=x` signed every
session token in the deployment with roughly seven bits of entropy and
produced no error, no warning, and no visible symptom. The `regstack
doctor` command flagged it, but only for operators who thought to run
it. That check is now enforced where the secret is first used to sign:
building a `RegStack` with a short secret raises `ValueError` at
startup, naming the length it got and the length it needs.

The second leak was in the hook system. Every registered handler for
`verification_requested`, `password_reset_requested` and
`email_change_requested` received a `url` keyword argument holding the
link that had just been emailed — including the raw single-use token.
Handlers that log their keyword arguments wholesale, the ordinary
shape of an audit or analytics webhook, were writing live
password-reset tokens into log aggregators, where anyone with
log-read access could use them to take over an account. Those three
events now also carry `url_without_token`: the same URL with the token
replaced by `[REDACTED]`. Redaction matches the token string itself
rather than a `token=` query key, so it holds for hosts that use the
URL templates to put the token in a path segment or hash fragment.

#### Added

- `url_without_token` keyword argument on the `verification_requested`,
  `password_reset_requested` and `email_change_requested` hook events —
  a loggable copy of the emailed link with the single-use token
  replaced by `[REDACTED]`. Exported as `regstack.hooks.redact_token`
  for hosts composing their own links.
- `MIN_JWT_SECRET_LENGTH` in `regstack.config.secrets`, shared by the
  runtime guard and the `regstack doctor` check so the two can't drift.

#### Changed

- `JwtCodec.__init__` rejects a `jwt_secret` shorter than 32
  characters. Deployments running a short secret will fail to start
  until it is replaced; `regstack init` generates a suitable one.
  (Issue #153.)

#### Security

- Verification, password-reset and email-change hook events no longer
  force host handlers to choose between logging nothing and logging a
  live credential. (Issue #154.)

#### Documentation

- The per-route IP rate limits are now explicitly flagged as
  default-off in both the configuration reference and the security
  model, with the reason the defaults stay off (turning them on would
  fail closed for hosts without the `rate_limit` extra).

#### Fixed

- `mypy` no longer reports an unused-ignore error on the lazy Twilio
  import. The inline ignore covered only the error code raised when the
  optional `twilio` extra is absent; a module-level override in
  `pyproject.toml` now covers both cases.

## 0.8.6 — 2026-07-19

### Headline

A changelog rendering fix — hyphenated words no longer break across
line wraps

The changelog is hard-wrapped at roughly seventy columns, which reads
well in the repository and on ReadTheDocs. Five hyphenated compounds
had landed with the wrap falling immediately after the hyphen, and
because Markdown joins a soft line break with a space, those words
rendered with a gap in the middle — `pydantic- settings` rather than
`pydantic-settings`. The defect showed up anywhere the file was
rendered rather than read as plain text: the Sphinx documentation, the
GitHub file view, and the release notes generated from it.

This release contains no code changes. The package installs and
behaves identically to 0.8.5, including the five security dependency
floors that release introduced. It exists so the published
documentation matches what was always intended, and so future release
notes can be generated from the changelog without a manual repair
step.

#### Fixed

- Five hyphenated compounds in `docs/changelog.md` no longer straddle
  a line wrap: `pydantic-settings`, `misleadingly-named`,
  `sender-domain`, `admin-promote-meets-user-clicks-verify`, and
  `Connected-accounts`. The affected paragraphs and bullets were
  rewrapped; the change is whitespace-only and the character sequence
  of the prose is unchanged.

## 0.8.5 — 2026-07-19

### Headline

Five security dependency-floor bumps resolving a cluster of CVEs that
have been tracked across the June–July 2026 daily security reviews.

All five changes are `pyproject.toml` floor raises with a matching
`uv lock` run — no product code was touched. The bumps protect
downstream consumers who resolve any affected version within the
previously-permitted range; the locked versions in 0.8.4 were already
safe for three of the five packages (starlette, aiosmtplib,
pydantic-settings resolved one version below the safe threshold), but
the floor must match the safe version so `pip install regstack` with
no lock file doesn't silently install a vulnerable dependency.

### Security

- **python-multipart floor raised to `>=0.0.32`.** Closes a six-CVE
  cluster: CVE-2026-40347 (DoS via oversized multipart preamble),
  CVE-2026-42561 (DoS via unbounded part-header count/size, CVSS 7.5),
  CVE-2026-53537 (RFC 2231/5987 Content-Disposition header smuggling),
  CVE-2026-53538 (`;`-separator WAF bypass — parameter smuggling),
  CVE-2026-53539 (CVSS 7.5 — `QuerystringParser` O(B²) DoS with `;`
  separators, directly reachable via login/register in any regstack
  host), and CVE-2026-53540 (negative `Content-Length` turns bounded
  read into read-until-EOF — memory DoS). `>=0.0.32` is the first
  version clear of all six. (Security reviews 2026-06-23 – 2026-07-19,
  W-1.)

- **starlette floor raised to `>=1.3.1`.** Closes a four-CVE cluster
  affecting 1.0.0–1.3.0: CVE-2026-48710 ("BadHost" — malformed `Host`
  header bypasses path-based auth; fixed in 1.0.1), CVE-2026-48818
  (Windows `StaticFiles` UNC path leaks NTLMv2 hash; fixed in 1.1.0),
  CVE-2026-54282 (attacker-controlled `request.url.hostname` via non-`/`
  path; fixed in 1.3.0), and CVE-2026-54283 (`x-www-form-urlencoded`
  ignores `max_fields`/`max_part_size` — unauthenticated DoS; fixed in
  1.3.1). The `uv.lock` already resolved 1.3.1; this is a floor-only
  fix that protects consumers who resolve without a lock file. (Security
  reviews 2026-06-24 – 2026-07-19, W-2.)

- **cryptography floor raised to `>=48.0.1`** (in the `oauth` extra and
  `dev` extra). Closes CVE-2026-45447 (heap use-after-free in
  `PKCS7_verify()` triggered by a crafted empty `digestAlgorithms` SET
  in a PKCS#7/S/MIME message — CVSS 7.5) in addition to the three CVEs
  already covered by the previous `>=46.0.7` floor (CVE-2026-26007,
  CVE-2026-34073, CVE-2026-39892). The 0.8.4 lock resolved 47.0.0,
  which is in the affected range. (Security reviews 2026-06-26 –
  2026-07-19, W-3.)

- **aiosmtplib floor raised to `>=5.1.1`.** Closes CVE-2026-53533
  (SMTP command injection, CVSS 6.5): `SMTP.mail()`, `SMTP.rcpt()`,
  `SMTP.vrfy()`, and `SMTP.expn()` did not reject C0 control characters
  (including CR/LF) in caller-supplied email addresses, allowing
  wire-level SMTP command injection below Jinja2 template rendering.
  Practical risk is reduced by `EmailStr` validation rejecting literal
  CR/LF, but the floor must be correct regardless. (Security reviews
  2026-06-26 – 2026-07-19, W-4.)

- **pydantic-settings floor raised to `>=2.14.2`.** Closes
  CVE-2026-32690 (CWE-22: `validate_secrets_path()` size-check skips
  symlinked directories, enabling `secrets_dir_max_size` bypass) and
  CVE-2026-58203 (CWE-59: `NestedSecretsSettingsSource` follows
  symlinks outside the configured secrets tree). `RegStackConfig`
  extends `BaseSettings`; deployments that point the secrets source at a
  directory where an attacker can place symlinks are at risk. (Security
  reviews 2026-06-26 – 2026-07-19, W-5.)

- **CVE-2026-48524 added to pyjwt comment** (editorial, no floor change).
  The `pyjwt>=2.13.0` floor was already correct; the inline comment
  in `pyproject.toml` was missing this CVE (connection-pool exhaustion
  via fresh JWKS fetch on every cache miss) from the June-2026 cluster.
  Enumerated alongside the other five CVEs it already listed. (Security
  review 2026-07-19, I-4.)

## 0.8.4 — 2026-06-22

### Headline

A security dependency-floor bump for the June-2026 PyJWT and Starlette
CVEs, plus a coverage routine that survives a locked-down network.

The shipped change in this release is two raised dependency floors.
PyJWT moves to `>=2.13.0`, closing the June-2026 `PyJWKClient` advisory
cluster — an algorithm allow-list bypass on `get_signing_key_from_jwt`
(CVE-2026-48523), algorithm confusion (CVE-2026-48526), an SSRF via the
JWKS URI scheme (CVE-2026-48522), and an unauthenticated DoS in
detached-payload JWS (CVE-2026-48525). The first two sit directly on
regstack's Google OAuth ID-token verification path, which calls exactly
`get_signing_key_from_jwt` + `decode`. Starlette is pinned `>=1.0.1` for
CVE-2026-48710 ("BadHost"), a `Host`-header path-injection that bypasses
path-based auth; regstack doesn't route auth off `request.url.path`, but
Starlette ships transitively to every host, so the floor closes the door
for them too.

The rest of this release is development-side hardening that never reaches
the published wheel. The weekly coverage routine now provisions MongoDB
from the official static tarball — verified against a pinned per-arch
SHA-256 — when a restricted-network CI container blocks the apt repo, so
it produces a full-matrix number instead of failing outright. And the
coverage measurement itself was made honest: the optional SES/SNS/Twilio
backends are now actually exercised, the untestable native-GUI shims are
excluded, and the SMS/SES/validate surfaces were backfilled with tests,
taking line coverage from 85% to 89%.

### Security

- **PyJWT floor raised to `>=2.13.0`.** Closes the June-2026
  `PyJWKClient` cluster: CVE-2026-48523 (algorithm allow-list bypass on
  the `get_signing_key_from_jwt` flow), CVE-2026-48526 (algorithm
  confusion via a public JWK used as an HMAC secret), CVE-2026-48522
  (SSRF via `PyJWKClient` URI-scheme handling), and CVE-2026-48525
  (unauthenticated DoS in detached-payload JWS decode). regstack's Google
  OAuth ID-token verification uses `get_signing_key_from_jwt` + `decode`
  directly, so 48523/48526 are on its verification code path. Bumped in
  the base dependencies, the `oauth` extra (alongside the existing
  `cryptography>=46.0.7`), and the `dev` extra; `uv.lock` resolves PyJWT
  2.13.0. (Security review 2026-06-20 · W-1.)

- **Starlette floor pinned `>=1.0.1`.** Closes CVE-2026-48710
  ("BadHost"): a `Host` header containing `/`, `?`, or `#` poisons the
  re-parsed `request.url.path`, bypassing path-based auth checks.
  regstack doesn't make auth decisions off `request.url.path`, but
  Starlette ships transitively (via FastAPI) to every host, so pinning
  the floor protects hosts that do. `uv.lock` resolves Starlette 1.3.1.
  A test-client-only `StarletteDeprecationWarning` (httpx2) introduced by
  Starlette ≥ 1.3 is silenced with a targeted `filterwarnings` ignore
  rather than by capping the floor. (Security review 2026-06-20 · W-2.)

### Development

- **Restricted-network MongoDB provisioning for the coverage routine.**
  `scripts/ccr_coverage_setup.py` now falls back to the official static
  `mongod` tarball from `fastdl.mongodb.org` when both apt paths fail —
  the HTTP 403 a locked-down CI container returns on the
  `repo.mongodb.org` apt repo. The download is verified against a pinned
  per-arch SHA-256 before extraction, so a corrupted or tampered tarball
  is rejected rather than installed; the tag is pinned to `ubuntu2004`,
  the oldest-glibc build that ships both x86_64 and aarch64 so one binary
  runs forward on any newer container. The routine now produces a
  full-matrix coverage number instead of `COVERAGE_INFRA_UNAVAILABLE`.

- **Honest coverage measurement, 85% → 89%.** The `dev` extra now
  installs `aioboto3` + `twilio` so the SES/SNS/Twilio backends and the
  SES setup wizard are exercised rather than skipped, and the native
  pywebview window-launch shims (untestable headless) are excluded from
  the report. The previously-untested SMS/SES backends, the SES wizard's
  AWS layer, and the `regstack validate` phases were backfilled with
  tests. All dev/CI tooling — no runtime change.

## 0.8.3 — 2026-06-12

### Headline

Closing out the May security-review series, an accurate
`was_new_account` on the OAuth exchange, and leak-proof test databases.

This release resolves every remaining finding from the 2026-05-21 and
2026-05-22 daily security reviews — and with them, the full
2026-05-15 → 2026-05-22 report series. Google ID-token verification now
puts a 5-second timeout on JWKS fetches, so a Google outage can no
longer pin asyncio worker threads indefinitely; OAuth token-exchange
failures keep the provider's verbose error body out of production
WARNING logs; and the two CVEs flagged for monitoring
(CVE-2026-2978/2979) were triaged as not applicable once their details
published — they affect the third-party "FastApiAdmin" project, not the
`fastapi` library regstack depends on.

SPA hosts get one behavioural fix: `/oauth/exchange` now reports
`was_new_account` accurately instead of hardcoding `False`, backed by a
new `oauth_states.result_was_new` column (Alembic migration `0003`), so
a "welcome, account created" prompt finally has something to key off.
On the development side, the test suite no longer leaks MongoDB
databases — per-test drops now cover every fixture path, and a
run-scoped sweep cleans up after crashed workers.

### Fixed

- **Google JWKS fetch now has a timeout.** `PyJWKClient` was built
  without a `timeout`, so its synchronous `urllib` fetch — offloaded to
  `asyncio.to_thread` since 0.8.2 — could pin a worker thread
  indefinitely during a Google JWKS outage. Under sustained OAuth load
  that risks exhausting the bounded asyncio thread pool, stalling every
  `to_thread` call in the app. Added `JWKS_FETCH_TIMEOUT_SECONDS = 5`.
  (Security review 2026-05-22 · W-1.)

- **`/oauth/exchange` reports `was_new_account` accurately.** The field
  was hardcoded `False`. The callback already computes whether it created
  a brand-new account but had no field to persist it on, so SPAs relying
  on the flag to show a "welcome, account created" prompt never saw it.
  Added `oauth_states.result_was_new` (Alembic migration `0003`,
  `batch_alter_table` ADD COLUMN with a `False` server default for
  SQLite). The callback sets it on both the session-token and
  MFA-pending branches; the exchange endpoint returns it.
  (Security review 2026-05-22 · I-1.)

### Security

- **OAuth token-exchange errors no longer log the full provider response
  body at WARNING.** On a non-200 token exchange, `google.py` now logs
  the response body at DEBUG and raises `OAuthTokenExchangeError` carrying
  only `HTTP <status>` — which the router logs at WARNING. The body
  doesn't contain our client secret or the auth code, but there's no
  reason to put Google's verbose error JSON in production WARNING logs.
  (Security review 2026-05-22 · I-3.)

- **CVE-2026-2978 / CVE-2026-2979 triaged: not applicable.** The
  2026-05-22 review flagged two then-undetailed FastAPI-adjacent CVEs
  for monitoring. Published NVD details show both affect the
  third-party "FastApiAdmin" project (unrestricted-upload flaws in its
  own controllers), not the `fastapi` library; regstack has no
  `fastapi-admin` dependency, and no fastapi-core advisories affect
  versions ≥ 0.120.0 (our floor; 0.136.x current). Triage note recorded
  next to the floor in `pyproject.toml` so future reviews don't
  re-raise it. (Security review 2026-05-22 · I-2.)

### Fixed (test infrastructure)

- **`FrozenClock` now defaults to 2125-01-01 — the future, not the
  past.** MongoDB's TTL monitor reaps documents by comparing
  `expires_at` against real wall-clock time every ~60s; it knows
  nothing about the injected test clock. With the old 2025-01-01
  default, every TTL-indexed row a test wrote (`oauth_states`,
  `pending_registrations`, `mfa_codes`, `login_attempts`, blacklist)
  was born already-expired, and a test whose flow straddled a TTL sweep
  had its rows deleted mid-flight — a rare, mongo-only parallel flake
  (caught as `test_link_flow_attaches_to_authenticated_user[mongo]`
  losing its `oauth_states` row between link-start and callback). A
  far-future pin keeps the reaper permanently out of reach while
  staying deterministic.

- **Test runs no longer leak MongoDB databases.** Tests built on the
  `make_client` factory never went through the `regstack` fixture's
  teardown, leaking one `regstack_test_*` database per test per run
  (6,625 had accumulated locally). A `_ensure_mongo_db_dropped` fixture
  wired into `config` now drops the per-test DB on every path, every
  test-run's DB names carry a run token so a `pytest_sessionfinish`
  sweep on the xdist controller removes anything a crashed worker left
  behind, and `regstack doctor`'s CLI test drops its DB in a `finally`.
  `inv clean-test-dbs` purges leftovers from hard-killed runs.

### Documented

- **`phone_number` in the admin user listing.** `docs/security.md` now
  documents that `UserPublic.phone_number` is returned in plaintext on
  `GET /admin/users` (a superuser can bulk-export every user's MFA phone
  number, which is regulated PII in some jurisdictions) and that hosts
  who treat it as restricted should wrap the admin listing in their own
  masked/omitting response model. The field stays on the default
  projection so `/me` and the admin detail view remain simple — the
  masking policy is a host decision driven by the deployment's
  regulatory context. (Security review 2026-05-21 · I-2.)

## 0.8.2 — 2026-05-21

### Headline

Security hardening from two daily reviews, and a `doctor` check that
nudges operators off vulnerable MongoDB servers.

This release closes the findings from the 2026-05-19 and 2026-05-20
security reviews. `regstack doctor` learns a new advisory check: on a
Mongo backend it reads the connected server's version and warns when
it's behind the CVE-2025-14847 ("MongoBleed") patched baseline — a
server-side bug the pymongo driver isn't exposed to, but one host
operators should still patch. The warning is advisory (a yellow ⚠),
so it surfaces the risk without failing `regstack doctor && deploy`.

Two smaller security fixes round it out: the `null` SMS backend no
longer logs the 6-digit MFA code by default (symmetric with the email
backend, so a misconfigured deployment can't leak codes into shared
logs), and Google ID-token verification no longer runs its JWKS fetch
on the event loop. Plus the weekly coverage routine now survives
restricted-network CI containers instead of refusing to produce a
number.

### Added

- **`regstack doctor` flags out-of-date MongoDB servers.** A new
  advisory check (mongo backend only) compares the connected server's
  version against the CVE-2025-14847 ("MongoBleed") patched baseline and
  prints a ⚠ warning when it's behind. Advisory warnings are surfaced
  but do **not** fail the command (exit stays 0), so
  `regstack doctor && deploy` is unaffected. `CheckResult` gained a
  `warn` state and doctor renders three symbols now: ✔ / ⚠ / ✘.
  Minimum supported server versions are documented in
  [embedding.md](embedding.md). (Security review 2026-05-20 · I-3.)

### Security

- **`sms.log_bodies` now defaults to `False`.** The `null` SMS backend
  used to log the message body (including the 6-digit MFA code) at INFO
  by default, asymmetric with `email.log_bodies`. A misconfigured
  deployment left on the `null` backend could leak codes into shared
  logs. The default is now off; flip it on for local dev when you want
  the code in stdout (the bundled examples surface it via an
  `mfa_login_started` hook, so they're unaffected). (Security review
  2026-05-19.)

### Changed

- **OAuth JWKS fetch no longer blocks the event loop.** Google ID-token
  verification ran `PyJWKClient.get_signing_key_from_jwt` (synchronous
  `urllib`) directly on the event loop; a JWKS cache-miss fetch could
  stall concurrent requests for the round-trip to Google. It now runs
  via `asyncio.to_thread`. (Security review 2026-05-20 · I-2.)

- **Coverage routine survives restricted-network containers.** The
  weekly CCR coverage run used to refuse any partial-matrix
  invocation outright (issue #71). `scripts/ccr_coverage_setup.py`
  is now tolerant: each backend (Mongo, Postgres, Playwright) is
  attempted independently and the script reports a status table
  showing which came up; failures don't abort siblings. `inv
  coverage` gained `--backends=<csv>` (default
  `sqlite,mongo,postgres`) and `--allow-partial`. The default
  still hard-fails when something's missing — partial-matrix
  numbers are not a release gate — but `--allow-partial` produces
  a number with a `COVERAGE_PARTIAL` banner naming the excluded
  backends, which is useful for tracking trends in restricted
  environments. CI continues to run the full matrix on every push.

## 0.8.1 — 2026-05-19

### Headline

CLI ergonomics: one `--config` flag everywhere, `--headless` and
`--dry-run` on the wizards.

The 0.8.0 system-consistency review left five deferred items on the
table. Three of them ship in this release. Every command that loads
or writes regstack config now takes a single `--config` flag — and
it accepts either a TOML file path or a directory containing one, so
the same value flows through `init` and the read-only commands without
the old file-vs-directory dance. `--target` survives as a deprecated
alias on the four config-writing commands (`init`, `oauth setup`,
`ses setup`, `theme design`) and emits a one-line warning; it goes
away in 1.0. `migrate --target` keeps its existing meaning (Alembic
revision) since the conflict is contextual — the helptext now spells
that out.

Alongside that, the three pywebview wizards swap their
misleadingly-named `--print-only` flag (which actually wrote to disk)
for two honest flags: `--headless` writes the config from CLI flags
and prints a JSON summary; `--dry-run` validates and prints the same
diff but leaves the filesystem untouched. `--print-only` survives as a
deprecated alias for `--headless`; it also goes away in 1.0. The
emitted JSON now carries `"dry_run": <bool>` so scripted consumers
can branch on it.

Plus a small refactor: the two SSR token-handoff pages
(`verify.html`, `email_change_confirm.html`) used to be near-duplicate
templates. They now share a Jinja macro, `auth/_token_handoff.html` —
rendered HTML is unchanged, but the divergence is gone for the next
time someone needs to touch both at once.

### Changed

- **CLI flag unification.** `--config` is now the canonical flag on
  every command that loads or writes regstack config. Accepts either
  a path to `regstack.toml` or a directory containing it. `--target`
  is retained as a deprecated alias on `init`, `oauth setup`,
  `ses setup`, and `theme design` (warning emitted; removed in 1.0).
  `migrate --target` keeps its existing meaning (Alembic revision)
  since the conflict is contextual.

- **Wizard mode flags.** The three pywebview wizards now expose
  `--headless` ("skip the GUI, write the config from CLI flags,
  emit a JSON summary") and `--dry-run` ("validate and print the
  diff but do **not** touch the files"). `--print-only` is the
  deprecated alias for `--headless`; removed in 1.0. The emitted
  JSON gains a `"dry_run": <bool>` field for scripted consumers.

- **Token-handoff SSR pages share a macro.** `verify.html` and
  `email_change_confirm.html` were near-duplicates — same DOM,
  same `data-rs-token` / `data-rs-status` shape, only the heading
  and pending-message strings differed. The shared body lives in
  `auth/_token_handoff.html`. Rendered HTML is unchanged; hosts
  that override either file individually still win against the
  bundled default via `RegStack.add_template_dir(...)`.

### Deprecated

- `--target` on `regstack init`, `regstack oauth setup`,
  `regstack ses setup`, `regstack theme design`. Use `--config`.
  Removed in 1.0.
- `--print-only` on `regstack oauth setup`, `regstack ses setup`,
  `regstack theme design`. Use `--headless` (or `--dry-run` for
  no-write validation). Removed in 1.0.

## 0.8.0 — 2026-05-19

### Headline

`regstack ses setup` — guided SES configuration that validates
against AWS as you go.

A new pywebview wizard mirrors the existing `regstack oauth setup`
flow: pick a region, pick a credential source (named profile /
explicit access keys / IAM role chain), confirm the sender domain
is verified in SES, detect the AWS account's sandbox state, fire
one live test send, then merge the result into `regstack.toml`
+ `regstack.secrets.env` non-destructively. Available behind both
`wizard` and `ses` extras: `pip install 'regstack[wizard,ses]'`.

Plus two security fixes from yesterday's daily review (the
theme-designer wheel was shipping well-known example credentials in
its preview HTML; the Google OAuth exchange path could echo a live
short-lived access token in an error message when the response
shape was malformed).

### Added

- **`regstack ses setup` CLI command.** Guided pywebview wizard for the
  SES email backend. Nine steps walk through region selection,
  credential source (`profile` / `explicit` / `chain`), sender-domain
  identity verification (via SES `GetIdentityVerificationAttributes`),
  sandbox detection (via `GetAccount` with a `GetSendQuota` heuristic
  fallback for IAM-restricted policies), and a live test send (via
  `SendEmail`). Non-clobbering tomlkit + secrets.env merge.

  Headless `--print-only` mode runs the same merge without the GUI
  for CI / scripting. Gated behind the joint extra:
  `pip install 'regstack[wizard,ses]'`. Companion to the existing
  `regstack doctor --send-test-email` for post-config verification.

### Security

- **Theme-designer preview no longer ships well-known credentials.**
  The `designer.html` preview card had `alice@example.com` /
  `hunter2hunter2` as `value=` attributes on its mock sign-in form,
  meaning every wheel that bundled the wizard carried well-known
  example creds someone could mistake for real fixtures. Both are
  now `placeholder=` attributes so the form starts empty. (Daily
  security review 2026-05-18 · I-1.)

- **Google OAuth `exchange_code()` no longer echoes the response
  body.** In the rare 200-without-`id_token` edge case
  (misconfigured client missing the `openid` scope), the response
  body can carry a live short-lived `access_token`. The previous
  `OAuthTokenExchangeError(f"... {body!r}")` then surfaced that
  token through the router's WARNING-level log. Dropped `{body!r}`
  from the exception message; regression test
  `test_exchange_code_error_message_does_not_leak_token_body`
  plants a recognisable token in the response and asserts it
  never appears in the exception's `str()` or `args` tuple. (Daily
  security review 2026-05-18 · I-2.)

## 0.7.0 — 2026-05-17

### Headline

`regstack validate` end-to-end probe, seven security-review findings closed, and a clutch of host-integration ergonomic wins.

Two-week sprint covering 21 commits since 0.6.0. The headline feature is `regstack validate` — a new CLI command that drives a real deployed install through every auth flow (register, verify, login, logout, password reset, change-email, OAuth start, SMS 2FA) from a remote operator workstation, scraping one-time tokens out of the deployment's stdout via a `--log-source` of your choice (`file:`, `ssh:`, `docker:`, `cmd:`). It is the companion to `regstack doctor`: doctor checks the loaded config, validate checks the running service.

Alongside `validate`, the release lands a handful of integration-ergonomic wins for embedding hosts — per-link email URL templates so SPAs with non-canonical routes don't need to override templates, `current_user_optional` for endpoints that render differently for anonymous callers, `RegStack.promote_pending` for admin rescue of stuck signups, and explicit SES credential fields so hosts loading AWS creds from a secrets store don't need to re-export them as environment variables. Two breaking trims (`UserPublic._id` → `id` and `TokenTransport = Literal["bearer"]` only) drop dead API surface that was already broken or misleading.

The security half of the release closes everything outstanding from the 2026-05-15 and 2026-05-16 daily reviews — the python-multipart CVE-2026-42561 floor bump, sdist-leak exclusions, defensive ObjectId guards on nine Mongo mutations, rate-limit coverage for `/login/mfa-confirm` and `/oauth/exchange`, OAuth callback log-injection sanitization, MongoDB-side `oauth_states.mode` validation, the alembic 0002 downgrade NULL guard, PEP 740 sigstore attestations on the publish workflow, and the removal of `workflow_dispatch` from `publish.yml`. The 2026-05-17 review (`docs/security-reports/2026-05-17.md`) verified all of them resolved.

### Changed (BREAKING)

- **`UserPublic` serialises the user identifier as `id`, not `_id`.**
  The `alias="_id"` on `UserPublic.id` (with `populate_by_name=True`)
  is removed. Every endpoint returning a `UserPublic` —
  `POST /api/auth/register`, `GET /api/auth/me`,
  `PATCH /api/auth/me`, the admin user endpoints — now sends `id`
  on the wire. `BaseUser` (the Mongo-document model) keeps the
  alias because `to_mongo()` round-trips via `model_dump(by_alias=True)`;
  the API surface is the only thing that changes.

  **Migration:** any client (browser or service) that read
  `body["_id"]` should switch to `body["id"]`. Hosts that bolted
  a hand-rolled `/me` override on top of the previous shape to get
  a conventional `id` key can drop that adapter entirely.

- **`TokenTransport` literal narrowed to `Literal["bearer"]`.**
  `"cookie"` was previously accepted by config validation but
  silently no-op'd (no router ever set `Set-Cookie`). Hosts that
  set `transport = "cookie"` now get a pydantic `literal_error`
  at startup instead of a silent security misconfiguration.
  `RegStackConfig.cookie_domain` is removed along with it.
  `regstack init` no longer offers the cookie option either.

### Added

- **`regstack validate` CLI command.** End-to-end probe of a
  deployed install — registers a throwaway user, walks every
  auth flow, then deletes the user. Reads one-time tokens out of
  the deployment's stdout via `--log-source` (file / ssh / docker
  / arbitrary command). Skip phases with `--skip`. Companion to
  `regstack doctor` (which only validates loaded config). See
  `regstack validate --help` for the full operator runbook
  (preparation steps + phase list).
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
- **Per-link email URL templates** for SPAs whose router shape
  doesn't take the canonical `/verify?token=...` /
  `/reset-password?token=...` /
  `/confirm-email-change?token=...` form. Three new optional
  `RegStackConfig` fields:

      verify_url_template: str | None = None
      password_reset_url_template: str | None = None
      email_change_url_template: str | None = None

  Each accepts `{base_url}` (trailing slash trimmed) and `{token}`
  (literal, no URL-encoding) placeholders. A hash-routed SPA can
  set `verify_url_template = "{base_url}/#/verify/{token}"`; a host
  whose auth pages live on a sibling subdomain can set
  `verify_url_template = "https://auth.example.com/verify/{token}"`.

  When a template is `None`, the resolver falls back to the
  `email_link_prefix`-based composition above — no behaviour
  change for anyone who hasn't opted in. New helpers
  `RegStackConfig.resolve_verify_url(token)`,
  `resolve_password_reset_url(token)`,
  `resolve_email_change_url(token)` are now the single source of
  truth for those URLs across the four router call sites.
- **`current_user_optional` dependency.** Companion to the
  existing `current_user` / `current_admin` factories on
  `regstack.deps`. Returns `BaseUser | None` instead of raising
  401 — for endpoints that render differently for authenticated
  vs anonymous callers (cart icon, comment author prefill,
  "your recent X" sections):

      from fastapi import Depends
      from regstack.models.user import BaseUser

      @app.get("/products/{slug}")
      async def view_product(
          slug: str,
          user: BaseUser | None = Depends(regstack.deps.current_user_optional()),
      ):
          ...

  Every form of auth failure — missing header, wrong scheme,
  malformed/expired/revoked token, deleted or bulk-revoked user —
  collapses to `None` (no `HTTPException` raised). On success the
  user is stashed on `request.state.regstack_user`, same as
  `current_user`.

- **`RegStack.promote_pending(email)` + admin route** for converting
  a `PendingRegistration` row directly into a verified active user,
  bypassing the email-link round-trip:

      user = await regstack.promote_pending("alice@example.com")

  The pending row's `hashed_password` and `full_name` carry over
  verbatim so the user can log in with their original password.
  Fires the same `user_verified` hook as `POST /verify` so
  downstream analytics see one event regardless of trigger.

  Useful for:
  - admin rescue of users who lost their verification link;
  - CLI batch seeding from a known-good list;
  - dev fixtures.

  Exposed over HTTP as `POST /admin/pending/{email}/promote` when
  the admin router is enabled. Returns 201 on success, 404 when no
  pending row exists for that email (also when the row has
  expired past its TTL), 409 when a user with that email is
  already registered.

- **Explicit SES credential fields on `EmailConfig`.** Two new
  optional `SecretStr` fields:

      ses_access_key_id: SecretStr | None = None
      ses_secret_access_key: SecretStr | None = None

  Set them to pass AWS credentials directly into the SES backend
  instead of relying on boto3's environment-variable fallthrough.
  Useful when the host already loads its AWS creds from a secrets
  store (Vault, AWS Secrets Manager, `secrets.env`) and would
  rather not re-export them into the process environment.

  Validated as a pair: setting just one raises a `ValidationError`,
  and combining them with `ses_profile` raises (boto3 silently
  resolves explicit creds over profile, which makes the active
  identity ambiguous from config alone). Default unset → no
  behaviour change — boto3's existing credential chain (env vars,
  shared credentials file, EC2/ECS instance profile) keeps working.

### Security

- **CVE-2026-42561 — `python-multipart>=0.0.27`.** Closes a
  network-exploitable DoS via unbounded multipart part-header
  parsing (CVSS 7.5). Previous floor `>=0.0.26` had the earlier
  CVE-2026-40347 fix only.
- **sdist no longer ships internal docs to PyPI.** Added a
  `[tool.hatch.build.targets.sdist]` exclude block to
  `pyproject.toml`. The published source tarball used to contain
  `CLAUDE.md` (with a developer home-directory path), the
  security-review prompt, the full test suite, build tooling, and
  (when built from a worktree) a `.git` text file pointing at the
  operator's worktrees directory.
- **Defensive `ObjectId.is_valid()` on nine Mongo UserRepo
  mutations.** `set_last_login`, `set_tokens_invalidated_after`,
  `update_password`, `set_active`, `set_superuser`,
  `set_full_name`, `set_phone`, `set_mfa_enabled`, and
  `update_email` now match `get_by_id` / `delete`: invalid input
  no-ops instead of raising `bson.errors.InvalidId` (which would
  have surfaced as a 500 on any future caller passing raw external
  input).
- **Per-IP rate-limit map covers `/login/mfa-confirm` and
  `/oauth/exchange`.** Two new config fields:
  `login_mfa_confirm_rate_limit`, `oauth_exchange_rate_limit`. The
  per-code attempt counter on `mfa_codes` defends each individual
  code; this adds the per-IP layer against distributed guessing
  across many source IPs.
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

### Fixed

- **`regstack doctor --send-test-email` honours the new
  `from_name` fall-back.** Before, the probe path passed
  `config.email.from_name` (now `Optional[str]`) straight into
  `EmailMessage.from_name` (typed `str`), producing a
  `None <addr>` From: header when unset.
- **`install_schema()` survives a legacy unnamed unique-on-email
  index.** A host that previously ran
  `db.users.create_index([("email", 1)], unique=True)` from its
  own pre-regstack auth code has a Mongo-auto-named `email_1`
  index. `install_indexes` previously crashed on first boot with
  `IndexOptionsConflict` because it tried to create
  `email_unique` over the same key. It now detects any
  uniquely-indexed-by-email index whose name isn't
  `email_unique`, drops it, and proceeds. Idempotent — re-running
  on a healthy database leaves indexes alone.
- **`POST /verify` no longer 500s on the
  admin-promote-meets-user-clicks-verify race.** The endpoint now
  catches `UserAlreadyExistsError` from `users.create` and returns a
  graceful 400 ("This email is already registered. Please sign in.")
  instead of letting the unique-constraint violation bubble up as a 500.

### Internal

- **GitHub Actions pinned ahead of Node 20 deprecation.**
  `actions/checkout` v4→v6.0.2, `astral-sh/setup-uv` v3→v8.1.0,
  `actions/upload-artifact` v4→v7.0.1, `actions/download-artifact`
  v4→v8.0.1. All pins remain commit SHAs.
- **Daily scheduled security-review reports** land under
  `docs/security-reports/` for 2026-05-15 through 2026-05-17.
  The 2026-05-17 report is `[security-clean]`: all warnings from
  the prior two days were resolved in this release.

## 0.6.0 — 2026-05-14

### Changed (BREAKING — wizard install)

- **GUI setup wizards now require the optional `wizard` extra.**
  `regstack oauth setup` and `regstack theme design` previously
  worked from a bare `pip install regstack` because their deps —
  `pywebview`, `tomlkit`, and `uvicorn[standard]` — were in the base
  dependency list. That meant every library consumer (including pure
  FastAPI host apps that never run a setup wizard) was paying for a
  platform browser engine and an ASGI server at install time.
  Those three packages now live in a new `wizard` extra; the base
  install is significantly slimmer.

  **Migration.**

  - If you embed regstack but don't use the setup wizards: no
    action needed. `import regstack`, `RegStack(...)`, and the
    `regstack init` / `regstack doctor` / `regstack migrate` /
    `regstack create-admin` CLIs all work on the bare install.
  - If you use either setup wizard:
    `pip install 'regstack[wizard]'` or
    `uv sync --extra wizard`.
  - The `dev` extra continues to pull in the wizard deps directly,
    so `inv test-all` keeps working without an explicit `--extra
    wizard`.

  Running a wizard subcommand without the extra installed now
  exits with a one-line install hint and a non-zero exit code,
  rather than an `ImportError` traceback from deep inside the
  wizard subtree.

  Bumped to **0.6.0** rather than 0.5.12 because removing top-level
  dependencies can surprise downstream callers; the version line
  signals it's worth a glance at the migration note.

## 0.5.11 — 2026-05-14

CI / workflow hygiene. No runtime code changes.

### Security

- **All third-party GitHub Actions pinned to commit SHAs.**
  `actions/checkout@v4`, `astral-sh/setup-uv@v3`,
  `actions/upload-artifact@v4`, and `actions/download-artifact@v4`
  now use commit SHAs across both workflows (`pypa/gh-action-pypi-publish`
  was already SHA-pinned in 0.5.6). Tag swaps upstream can no longer
  substitute a malicious version.
- **`permissions:` blocks declared on every workflow + job.** Both
  workflows declare a `permissions: contents: read` default at the
  workflow level and re-state it per job. The `publish` job
  continues to add `id-token: write` for the OIDC trusted-publisher
  exchange — that's the only scope above read-only anywhere in
  the workflows.

### Internal

- `.gitignore` gained `.env`, `.env.*`, and the common credential-file
  patterns (`*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `*.crt`).
  Belt-and-braces for misconfigured local dev envs; nothing tracked
  today depends on these.

## 0.5.10 — 2026-05-14

Security fixes from the 2026-05-13 / 2026-05-14 daily review reports.

### Security

- **Open-redirect bypass in OAuth `redirect_to`.** `_validate_redirect`
  was forwarding `urlsplit`'s judgment, but browsers normalize values
  like `/\evil.com` and `////evil.com` into the protocol-relative
  `//evil.com`. Both forms now rejected.
- **CVE-2025-62727 — `fastapi>=0.120.0`** (was `>=0.110`). Starlette
  DoS via large request bodies after multipart processing.
- **CVE-2025-27516 — `jinja2>=3.1.6`** (was `>=3.1`). Sandbox breakout
  via the `|attr` filter.
- **Login lockout coverage extended.** `POST /login` was returning
  HTTP 403 for `is_active=False` and (when `require_verification=True`)
  unverified accounts **without** recording a failure — an attacker
  guessing passwords against either category had unbounded probing.
  The endpoint now:
  - Verifies the password **before** the active/verified checks (so
    an unauthenticated attacker can't distinguish disabled vs active
    accounts by HTTP code).
  - Records a failure before raising 403 in either branch.
- **`POST /change-email` anti-enumeration.** An authenticated
  attacker could walk the registered-email namespace via the 409 vs
  202 distinction. The endpoint now always returns 202; clashes are
  logged server-side and the confirmation email is silently skipped.
  Matches the existing stance on `/forgot-password` and
  `/resend-verification`.
- **Admin resend-verification rejects OAuth-only users** with a clear
  400 instead of attempting to construct a `PendingRegistration` with
  `hashed_password=None` (which corrupted the pending-registrations
  row).

## 0.5.9 — 2026-05-13

### Security

- **`OAuthConfig.enforce_mfa_on_oauth_signin` is now wired.** The flag
  has been on the config since 0.3.0 and surfaced through the OAuth
  setup wizard, but the callback never read it — operators who
  enabled it still got OAuth sign-ins that bypassed the SMS second
  factor. The High #1 finding from the post-0.5.6 consistency audit.

  When the flag is `true` and the resolved user has SMS MFA set up
  (`is_mfa_enabled=True` plus a `phone_number`), the OAuth callback
  now sends the SMS code, stashes a short-lived `login_mfa` pending
  JWT in the state row instead of a session token, and the SPA's
  `regstack.js` `oauth-complete` handler reroutes through the
  existing `/account/mfa-confirm` page → `POST /login/mfa-confirm`
  flow.

  Link flows (`mode="link"`) are intentionally exempt: the user was
  already authenticated when they kicked off the link, so re-MFAing
  is friction without a threat-model win.

### Changed

- `ExchangeResponse` gained two optional fields:
  - `mfa_required: bool = False`
  - `mfa_pending_token: str | None = None`

  `access_token` defaults to `""` (instead of being required) so the
  MFA branch can return without one. Existing SPAs that read
  `access_token` keep working — they just need to branch on
  `mfa_required` first.

- `oauth_signin_completed` hook now carries `mfa_required=<bool>`
  alongside the existing `user`, `provider`, `mode`, `was_new` kwargs
  so observability handlers can distinguish "session minted" from
  "MFA second-step in progress" outcomes.

## 0.5.8 — 2026-05-13

Audit-driven consistency cleanup — small fixes across the API surface
flagged by the post-0.5.6 consistency review.

### Security

- **`oauth.completion_ttl_seconds` is now enforced.** This flag has
  been on `OAuthConfig` since the OAuth router shipped, but the
  callback never used it. A state row stayed valid for the full
  `state_ttl_seconds` (300s default) between callback completion and
  the SPA's `/oauth/exchange` call. The callback now shortens the
  row's `expires_at` to `now + completion_ttl_seconds` (30s default)
  when it stashes the result token, so the blast radius of a stolen
  state_id post-callback is the documented 30-second window.

### Changed (UserPublic surface)

- `UserPublic` now serialises `updated_at` and
  `tokens_invalidated_after`. SPAs comparing the latter against their
  cached session JWT's `iat` can detect a forced sign-out after a
  password / email change without an extra round-trip.

### Changed (hook payloads)

- `oauth_signin_started` in `mode="link"` now carries the
  authenticated `user=` kwarg, matching `oauth_signin_completed` and
  `oauth_account_linked`. The `mode="signin"` call site stays
  user-less (there isn't one yet — sign-in is what produces it).

### Internal

- `OAuthStateRepoProtocol.set_result_token` grew an optional
  `new_expires_at=` kwarg so the callback can re-set the row's
  expiry atomically with the token write. Both Mongo and SQL impls
  updated.
- `MessageResponse` in `routers/oauth.py` deleted; the router now
  uses the shared one from `routers/_schemas.py`. OpenAPI no longer
  carries two identically-named schemas.
- `MongoBlacklistRepo.purge_expired` switched from `$lte` to `$lt`
  to match the rest of the `purge_expired` family. Bulk-revoke
  (which DOES use `<=` for the conservative same-instant
  interpretation) is unchanged.
- Dead `create()` / `delete_by_id()` methods removed from
  `MongoPendingRepo` — neither was in the protocol or the SQL impl.
- OAuth `start` and `callback` endpoints now declare
  `response_class=RedirectResponse` and `status_code=302`.
- Custom-claim JWT encoder in `routers/account.py` (email-change
  token) now emits `iat` as a float instead of `int`, matching the
  three other custom-claim encoders.
- `routers/verify.py` `created_at` for resent pending registrations
  now goes through `rs.clock.now()` instead of wall-clock
  `datetime.now(UTC)`.
- `BaseUser.model_config = ConfigDict(extra="allow")` is now
  documented inline (it's the only model in the package that
  doesn't `extra="forbid"`, on purpose).

## 0.5.7 — 2026-05-13

Documentation-only follow-up to 0.5.6 — no runtime code changes.

### Docs

- `docs/configuration.md` now documents the per-route `*_rate_limit`
  family (added in 0.5.4) instead of pointing at
  `login_max_per_minute` / `login_max_per_hour` as reserved future
  fields.
- `docs/security.md` no longer references
  `PasswordHasher.needs_rehash` (removed in 0.5.6). Replacement
  guidance points hosts at `pwdlib.PasswordHash.verify_and_update`
  inside a `user_logged_in` hook.
- Root `CHANGELOG.md` backfilled with 0.4.0 and 0.5.0 entries so it
  matches this file. The two changelogs are now in sync.

## 0.5.6 — 2026-05-13

A rollup release that consolidates 11 days of security-review
remediation, supply-chain hardening, a full `mypy --strict` pass,
and the per-route rate-limits feature.

### Added

- **Per-route IP rate limits.** Opt-in via the new `rate_limit` extra
  (or a host-supplied `slowapi.Limiter`) plus any of the new
  `RegStackConfig.*_rate_limit` fields (`login_rate_limit`,
  `register_rate_limit`, `forgot_password_rate_limit`,
  `reset_password_rate_limit`, `verify_rate_limit`,
  `resend_verification_rate_limit`, `change_password_rate_limit`,
  `change_email_rate_limit`, `confirm_email_change_rate_limit`,
  `delete_account_rate_limit`). Each accepts a slowapi-syntax string
  (`"5/minute"`, `"5/minute;20/hour"`).
- New constructor argument `RegStack(rate_limiter=...)`. When at
  least one `*_rate_limit` field is set, regstack expects either
  this argument or the `rate_limit` extra; failure to provide one
  raises `RuntimeError` on first access to `regstack.router` —
  failing closed beats silently disabling the protection. Hosts
  remain responsible for `app.state.limiter` and the
  `RateLimitExceeded` exception handler; slowapi owns the 429
  response shape.
- **`user_logged_out` hook now fires.** The event was listed in
  `KNOWN_EVENTS` since M1 but no router ever emitted it.
  `routers/logout.py` now fires `user_logged_out` (with a `user=`
  kwarg) immediately after the bearer token is revoked.

### Changed (security)

- **JWT 401 responses no longer leak the pyjwt error reason.**
  Replaced `f"Invalid token: {exc}"` with the static `"Invalid or
  expired token."`. The pyjwt error text disclosed *why* a token was
  rejected (signature mismatch, expired, malformed, audience
  mismatch) — useful signal for an attacker probing the auth
  surface.
- **OAuth sign-in honours `allow_registration=False`.** `/register`
  already did; the OAuth `_resolve_user` "brand-new account" branch
  did not, so an operator who disabled self-service signup still got
  accounts created via "Sign in with Google". The OAuth callback now
  redirects with `?error=registration_disabled` if no existing
  account matches and registration is disabled.
- **Admin `DELETE /admin/users/{id}` now cascades
  `oauth_identities`.** Matches the user-initiated `DELETE
  /account` flow; previously left orphan rows that blocked the
  Google subject from re-registering.
- **`POST /phone/start` and `DELETE /phone` guard against OAuth-only
  users.** Both endpoints previously crashed with HTTP 500 for
  users with `hashed_password=None`. Both now return 400 with a
  message pointing to forgot-password (which doubles as a "set
  initial password" path).

### Changed (BREAKING — hook contracts)

- **`mfa_login_started` and `phone_setup_started` no longer include
  the raw OTP code in their kwargs.** Hooks are best-effort
  observability and are the documented integration surface for
  analytics / logging / Slack notifications, so a plaintext OTP in
  `**kwargs` is a leak waiting to happen — a host adding
  `logger.info(kw)` to a hook handler is enough to put OTPs in a
  log stream. Hosts that subscribed to either event to take over
  SMS delivery should migrate to a custom `SmsService` subclass
  (the supported delivery override). The other kwargs (`user`,
  `phone`) remain.

### Changed (deps)

- `pyjwt>=2.12.1` (was `>=2.8`). Picks up CVE-2026-32597 (`crit`
  header bypass, CVSS 7.5).
- `cryptography>=46.0.7` added explicitly to the `oauth` extra
  (was pulled transitively, unbounded). Picks up CVE-2026-26007
  (ECC subgroup attack on the JWKS code path, CVSS 8.2) plus
  CVE-2026-34073 and CVE-2026-39892.
- `python-multipart>=0.0.26` (was `>=0.0.9`). Picks up
  CVE-2026-40347 (DoS via oversized multipart preamble).
- `pypa/gh-action-pypi-publish` in `publish.yml` pinned to a commit
  SHA instead of the mutable `release/v1` branch. The publish job
  holds `id-token: write`, so a tag/branch swap upstream would let
  an attacker push a malicious wheel under our OIDC identity.

### Removed

- `PasswordHasher.needs_rehash` — called pwdlib's non-existent
  `check_needs_rehash` and would `AttributeError` if anyone invoked
  it. No callers in src or tests. If you were planning to use it,
  call `pwdlib.PasswordHash.verify_and_update` directly.

### Internal

- 72 `mypy --strict` errors cleared across 35 files. `inv lint` is
  green end-to-end (ruff + mypy). Still local-only — not yet a CI
  gate.
- Mongo `BlacklistRepo.purge_expired` added (was missing from the
  Mongo impl; SQL impl already had it). Mongo's TTL index still
  reaps automatically; the explicit `delete_many` is for protocol
  parity and for tests that can't wait for the 60-second TTL
  monitor.
- `KNOWN_EVENTS` reconciled with reality: 7 previously-undeclared
  events added (`verification_requested`, `email_change_requested`,
  `email_changed`, `phone_setup_started`, `mfa_login_started`,
  `mfa_enabled`, `mfa_disabled`).
- `routers/_helpers.require_password_set` factored out of
  `routers/account.py` and reused in `routers/phone.py`.
- `AsyncDatabase[MongoDoc]` / `AsyncMongoClient[MongoDoc]`
  parameterized across the Mongo backend so pymongo's typed stubs
  are satisfied.

### Notes

- `LockoutService` (per-account, sliding-window failure counter) is
  unchanged and continues to defend `/login` against
  credential-stuffing against a single account. Per-route IP limits
  are orthogonal: they defend each endpoint against a single source
  IP spamming requests across many accounts.
- The previously-reserved `login_max_per_minute` /
  `login_max_per_hour` config fields are kept for back-compat but
  no longer have any effect. Switch to the per-route fields when
  you next touch your config.

## 0.5.0 — 2026-05-02

### Added

- **Theme designer.** `regstack theme design` opens a native pywebview
  window with controls for every `--rs-*` CSS custom property and a
  real-time preview of the bundled SSR widgets (sign-in form, success
  / error banners, danger-zone button). Saving writes
  `regstack-theme.css`; the designer round-trips values back into the
  form on next launch so iteration is non-destructive. `--print-only`
  mode takes repeatable `--var NAME=VALUE` pairs (with a `dark:`
  prefix for dark-scheme overrides) and writes the file headlessly.
  Lives in `regstack.wizard.theme_designer`; registered as a lazy
  Click subgroup so `regstack init` / `doctor` don't pay the
  pywebview/uvicorn import cost.
- "Why use regstack" pitch in `docs/index.md` updated to surface the
  two pywebview tools (`oauth setup` + `theme design`) as a
  distinguishing feature vs. fastapi-users / Auth0 / Keycloak.

### Docs

- New "About the examples" convention block at the top of
  `docs/index.md`. Every URL, email, smtp host, and admin command
  across the docs now extrapolates from the same fictional app at
  `app.example.com` with `<username>` / `<password>` placeholders —
  no more `user:pw@host/dbname` / `db.internal/myapp` mishmash.

## 0.4.0 — 2026-05-02

### Added

- **OAuth setup wizard.** `regstack oauth setup` opens a native
  webview window that walks an operator through registering a Google
  OAuth 2.0 client and merges the credentials into `regstack.toml` +
  `regstack.secrets.env` non-destructively (preserves comments, other
  tables, unrelated keys). 12-step SPA inside a local-only
  127.0.0.1 FastAPI server, gated by a per-launch random token. Each
  Next click hits a server-side validator so the Write step can never
  be reached with bad data. `--print-only` mode skips the GUI for
  headless / CI use.
- Three new base dependencies: `pywebview>=5.0`, `tomlkit>=0.13`,
  `uvicorn[standard]>=0.29` (the wizard's local server).
- `pytest-playwright` added to the `dev` extra; new `inv test-e2e`
  task chained into `inv test-all`.

## 0.3.0 — 2026-04-30

**OAuth — Sign in with Google.** Built across four PRs (M1–M4 of
[`tasks/oauth-design.md`](https://github.com/jdrumgoole/regstack/blob/main/tasks/oauth-design.md));
this is the release cut that wraps them up.

### Added

- New optional extra `oauth = ["pyjwt[crypto]>=2.8"]`.
- New `enable_oauth` flag and `OAuthConfig` sub-model
  (`google_client_id`, `google_client_secret`, `google_redirect_uri`,
  `auto_link_verified_emails`, `enforce_mfa_on_oauth_signin`,
  `state_ttl_seconds`, `completion_ttl_seconds`).
- `regstack.oauth` package — `OAuthProvider` ABC, `OAuthRegistry`,
  `OAuthTokens`, `OAuthUserInfo`, error hierarchy, and the concrete
  `GoogleProvider` (Authorization Code with PKCE, ID-token
  verification via `pyjwt[crypto]` + `PyJWKClient` against Google's
  JWKS).
- Five JSON endpoints (mounted lazily when `enable_oauth=True` and a
  provider is registered):
  - `GET    /oauth/{provider}/start`
  - `GET    /oauth/{provider}/callback`
  - `POST   /oauth/exchange`
  - `POST   /oauth/{provider}/link/start` (auth)
  - `DELETE /oauth/{provider}/link` (auth)
  - `GET    /oauth/providers` (auth)
- New SSR page `/account/oauth-complete` (token-handoff round-trip).
- "Sign in with Google" button on `/account/login` and a
  Connected-accounts panel on `/account/me`. Login page surfaces
  callback errors via `?error=<code>` with translated banners.
- Two new repo protocols: `OAuthIdentityRepoProtocol`,
  `OAuthStateRepoProtocol`. Mongo + SQL implementations with
  parametrized integration tests over all three backends.
- Four new hook events: `oauth_signin_started`,
  `oauth_signin_completed`, `oauth_account_linked`,
  `oauth_account_unlinked`.
- `tests/_fake_google/` — in-process provider stub so the OAuth
  test suite stays offline and parallel-safe.
- New docs page [`docs/oauth.md`](oauth.md) — host guide.

### Changed (potentially breaking)

- **`BaseUser.hashed_password: str` → `str | None`.** OAuth-only
  users have no password. The login route rejects password attempts
  on these accounts with the same generic 401 wrong-password gets
  (no enumeration). `change-password`, `change-email`, and
  `delete-account` all return 400 for OAuth-only users with a
  pointer at the password-reset flow, which doubles as a "set
  initial password" path.
- `users.hashed_password` is now nullable in the SQL schema —
  migration `0002_oauth.py` flips the column via `batch_alter_table`
  (SQLite-safe). Existing rows are unaffected.
- New SQL tables `oauth_identities` and `oauth_states`. Mongo
  collections + indexes added by `install_schema()`.

### Security defaults

- **Account-linking policy defaults to refuse.** When a Google
  sign-in carries an email that already belongs to a regstack user,
  the callback returns `?error=email_in_use`. Hosts can opt into
  auto-linking via `oauth.auto_link_verified_emails = true`, which
  also requires `email_verified=true` on the ID token. The threat
  model is in `tasks/oauth-design.md` § 1.
- **Server-side PKCE.** `code_verifier` is stored on the
  `oauth_states` row and never enters the URL.
- **One-time token-handoff.** `/oauth/exchange` consumes the state
  row atomically; second exchange returns 404.
- **Refuse to unlink the only sign-in method.** Returns 400 for
  OAuth-only users attempting to unlink their only provider.
- **OAuth sessions are normal session JWTs** — the existing
  `tokens_invalidated_after` bulk-revoke applies, so a password
  change kills any OAuth-issued session too.

### Migration notes

- Install the extra: `uv add 'regstack[oauth]'`.
- Configure: set `enable_oauth = true` and provide
  `oauth.google_client_id` + `oauth.google_client_secret` (the
  secret in `regstack.secrets.env` as
  `REGSTACK_OAUTH__GOOGLE_CLIENT_SECRET`).
- Schema: roll forward via `regstack migrate` or rely on
  `install_schema()` at first boot.

## 0.2.6 — 2026-04-28

**Bug fix.**

### Fixed

- ``/admin/stats`` reported ``pending_registrations: 0`` on every SQL
  backend. The route reached into the Mongo repo's private
  ``_collection`` attribute and silently fell back to ``0`` when the
  attribute was absent — the kind of failure that survives a
  multi-backend refactor when the integration tests don't pin the
  number.

### Added

- ``PendingRepoProtocol.count_unexpired(now=None) -> int``, with Mongo
  and SQL implementations. "Unexpired" rather than a raw row count
  because SQL backends accumulate dead rows until ``purge_expired``
  runs; an admin looking at "pending: 47" wants 47 *live* rows.
- The admin stats route now routes the count through
  ``rs.clock.now()``. Without this, ``FrozenClock``-driven tests
  would see every row as "expired" because the route would be reading
  wall-clock time while the rest of the system runs on the injected
  clock. Same shape of clock-injection drift the bulk-revoke fix
  closed earlier.
- New parametrized integration test
  ``test_stats_pending_registrations_count_unexpired`` runs against
  SQLite + Mongo + Postgres and confirms the count excludes expired
  rows on every backend.

## 0.2.5 — 2026-04-28

**Bug fix + tooling.**

### Fixed

- ``regstack doctor`` against a SQL backend crashed with
  ``asyncio.run() cannot be called from a running event loop``. The
  schema check called
  ``regstack.backends.sql.migrations.current()``, which used
  ``asyncio.run()`` internally — invalid inside doctor's own
  ``asyncio.run``. Added ``current_async()`` and switched the doctor
  command to use it. Sync ``current()`` is preserved for the migrate
  CLI (which runs outside an event loop).

### Added

- ``inv coverage [--no-html] [--fail-under=N]`` — runs the full
  three-backend matrix under coverage, combines per-pytest-xdist-worker
  ``.coverage`` files, prints the term-with-missing report, and writes
  ``htmlcov/``. Branch coverage is on by default.
- ``[tool.coverage.*]`` config in ``pyproject.toml``.
- ``tests/unit/test_cli_init.py`` — six tests driving the
  ``regstack init`` wizard via ``CliRunner(input=...)``. Lifts
  ``cli/init.py`` from 14% → 88%.
- ``tests/unit/test_cli_doctor.py`` — four tests for the SQLite
  ``regstack doctor`` paths. Lifts ``cli/doctor.py`` from 61% → 87%.

Total line coverage on the full backend matrix: **85% → 87.1%**
(branch coverage is also newly enabled).

## 0.2.4 — 2026-04-28

**Breaking** — every back-compat shim left over from the
multi-backend refactor has been removed.

### Removed

- `RegStack.install_indexes()` — the 0.1.x alias for
  `install_schema()`. Call `install_schema()`.
- `ObjectIdStr` alias for `IdStr` in `regstack.models._objectid`.
  Import `IdStr` directly.
- `__all__`-based re-exports of `UserAlreadyExistsError`,
  `PendingAlreadyExistsError`, `MfaVerifyOutcome`, and
  `MfaVerifyResult` from `regstack.backends.mongo.repositories.*` and
  the package `__init__`. Their canonical home is
  `regstack.backends.protocols`; that's where every consumer in the
  package itself already imports them.

### Migration

| Old                                                                              | New                                                            |
|----------------------------------------------------------------------------------|----------------------------------------------------------------|
| `await regstack.install_indexes()`                                               | `await regstack.install_schema()`                              |
| `from regstack.models._objectid import ObjectIdStr`                              | `from regstack.models._objectid import IdStr`                  |
| `from regstack.backends.mongo.repositories.user_repo import UserAlreadyExistsError`     | `from regstack.backends.protocols import UserAlreadyExistsError`     |
| `from regstack.backends.mongo.repositories.pending_repo import PendingAlreadyExistsError` | `from regstack.backends.protocols import PendingAlreadyExistsError`  |
| `from regstack.backends.mongo.repositories.mfa_code_repo import MfaVerifyOutcome, MfaVerifyResult` | `from regstack.backends.protocols import MfaVerifyOutcome, MfaVerifyResult` |

The internal Mongo helper
`regstack.backends.mongo.indexes.install_indexes(db, config)` is
unchanged — that's the function `MongoBackend.install_schema` calls
to actually create the indexes.

## 0.2.3 — 2026-04-28

**Docs-only release.** API reference rewritten around the current
package layout, public surface gained proper Google-style docstrings.

### Changed

- ``docs/api.md`` restructured around the post-multi-backend package
  layout (``regstack.backends.{base,protocols,factory,mongo,sql}`` and
  friends). Each section now opens with a one-paragraph orientation
  before the autodoc directives. The pre-refactor
  ``regstack.db.repositories.*`` references that rendered empty are
  gone.
- Added Google-style docstrings (purpose summary + Args / Returns /
  Raises) to the most-touched public methods on ``RegStack``,
  ``JwtCodec``, ``PasswordHasher``, ``LockoutService``,
  ``AuthDependencies``, ``HookRegistry``, ``EmailService``,
  ``SmsService``, ``build_router``, ``build_ui_router``,
  ``build_ui_environment``, ``default_static_dir``, ``Clock`` /
  ``SystemClock`` / ``FrozenClock``.
- Dataclass field documentation moved to PEP 258 attribute docstrings
  on ``TokenPayload``, ``LockoutDecision``, ``EmailMessage``,
  ``SmsMessage``, ``MfaVerifyResult`` — autodoc now renders each field
  with its description without the "duplicate object description"
  warnings the napoleon ``Attributes:`` block was triggering.
- ``MfaVerifyOutcome`` enum docstring reformatted as a bullet list
  (the napoleon ``Members:`` block isn't a recognised section).

## 0.2.2 — 2026-04-28

**Docs-only release.**

### Changed

- README and `docs/index.md` both now lead with the same pitch — a
  tagline ("Production-grade user accounts for your FastAPI app —
  without the vendor lock-in, the second service to run, or the
  homegrown auth bugs"), a "The problem regstack solves" section
  (Argon2, JWT revocation, account enumeration, bulk session
  invalidation, hashed one-time tokens, E.164 phone numbers), and a
  "Why not just use…?" comparison table covering hosted SaaS
  (Auth0 / Clerk / WorkOS / Stytch), self-hosted IAM (Keycloak /
  Authentik / Authelia / Ory Kratos), `fastapi-users`, and DIY.
- Trimmed hyperlink density back. Only major external packages,
  products, and JWT (RFC 7519) are linked. Wikipedia articles on
  CS concepts (façade pattern, multitenancy, idempotence, E.164,
  SHA-256, HMAC), MDN web platform basics (CSP, fetch, localStorage,
  HTTP 429, Retry-After, HTTPS, CSS custom properties), OWASP article
  links, Python stdlib pages, and deep-dependency helper-class docs
  (pwdlib, pydantic, asyncpg, pymongo, ChoiceLoader, TypeDecorator,
  StaticFiles, ProxyHeadersMiddleware, slowapi, APScheduler,
  pytest-xdist, Kubernetes probes) were removed.

## 0.2.1 — 2026-04-28

**Hotfix for 0.2.0.** `import regstack` was broken on any install that
didn't include the new `mongo` extra: `models/_objectid.py` imported
`bson` unconditionally, and four routers + the SQL MFA repo imported
shared error / enum types out of `regstack.backends.mongo.*`, which
in turn imports `pymongo` at module top level.

### Fixed

- `models/_objectid.py` now imports `bson.ObjectId` lazily inside a
  `try / except ImportError` and only uses it for `isinstance` checks
  when present.
- `UserAlreadyExistsError`, `PendingAlreadyExistsError`,
  `MfaVerifyOutcome`, and `MfaVerifyResult` moved from their backend
  modules to `regstack.backends.protocols` (the backend-agnostic
  location). Mongo modules re-export them for backwards compatibility.
- All consumer modules (`routers/register.py`, `routers/account.py`,
  `routers/login.py`, `routers/phone.py`, the SQL MFA repo) updated to
  import from `regstack.backends.protocols`.

### Added

- New `base-install-smoketest` CI job: builds the wheel and runs
  `import regstack` + a SQLite end-to-end RegStack lifecycle in a
  fresh venv with **no extras**. Will catch any future regression.
- New `tests/unit/test_base_install_imports.py` regression test that
  uses `sys.meta_path` to block `bson` / `pymongo` and confirm
  `import regstack` still succeeds.

## 0.2.0 — 2026-04-28

**Multi-backend support + Alembic migrations.** SQLite is now the
default; Postgres and
MongoDB are switched in by changing `database_url`. Embedding API
breaking change: `RegStack(config=, db=)` → `RegStack(config=,
backend=None)`; the backend is auto-built from the URL scheme.

This release also includes a documentation rewrite for less-expert
readers: the README and core docs now lead with the problem regstack
solves, hyperlink external standards (Argon2, RFC 7519, OWASP
enumeration, E.164, CSP, …), and compare regstack to the alternatives
(hosted SaaS, self-hosted IAM, `fastapi-users`, DIY).

### Added

- **Alembic migrations bundled.** `regstack.backends.sql.migrations`
  ships an in-package Alembic env (no `alembic.ini` on disk).
  `SqlBackend.install_schema()` runs `alembic upgrade head` instead of
  `MetaData.create_all`, so schema evolutions land as new revision
  files. New `regstack migrate [--target REV]` CLI for deploy-step
  migrations. New autogen-drift test catches `schema.py` ↔ migration
  mismatches before users see them.
- **`regstack doctor` schema check** is now Alembic-aware: it reports
  the deployed revision vs the bundled head and tells you to run
  `regstack migrate` if they diverge.
- **Per-backend invoke tasks**: `inv test-sqlite` (zero infra),
  `inv test-mongo` (needs local Mongo), `inv test-postgres
  [--url=...]` (needs local Postgres), `inv test-all` (all three).
  Driven by a new `REGSTACK_TEST_BACKENDS` env var that the
  parametrized backend fixture honours; mongo-only unit tests skip
  cleanly when mongo isn't in the active backend set.
- `regstack.backends.protocols` — `Protocol` classes for the five repos
  plus the shared `UserAlreadyExistsError`.
- `regstack.backends.base.Backend` ABC and
  `regstack.backends.factory.build_backend(config)` URL-scheme router.
- `regstack.backends.mongo` — relocated Mongo code with a `MongoBackend`
  class.
- `regstack.backends.sql` — new SQLAlchemy 2 async backend driving
  SQLite (aiosqlite) and Postgres (asyncpg). Five protocol-conforming
  repos, `UtcDateTime` TypeDecorator for cross-database tz-aware
  datetimes, `install_schema()` that creates all tables idempotently.
- `RegStack.aclose()` to tear down the backend's connection pool.
- `regstack.backends.protocols.UserRepoProtocol.purge_expired(...)` on
  every repo so SQL backends can drive cleanup uniformly (Mongo still
  relies on TTL indexes in normal operation).
- `examples/sqlite/`, `examples/postgres/`, `examples/mongo/` — one
  demo per backend sharing a FastAPI scaffold under `examples/_common/`.

### Changed

- `RegStackConfig.mongodb_url` → `database_url`. Default is
  `sqlite+aiosqlite:///./regstack.db`. `mongodb_database` retained for
  Mongo URLs without a `/dbname` path.
- `RegStack.install_indexes()` → `install_schema()` (alias kept).
- `UserRepo.count(filter_=...)` → `count(*, is_active=,
  is_verified=, is_superuser=)`.
- `UserRepo.list_paged(sort=...)` → `list_paged(*,
  sort_by_created_at_desc=)`.
- `MfaCodeRepo.find` returns `MfaCode | None` instead of `dict`.
- `regstack init` wizard asks which backend to use and writes the
  appropriate `database_url`.
- `regstack doctor` is backend-agnostic (`Backend.ping()`, schema check
  per backend kind).
- pyproject: `pymongo` and `asyncpg` moved to optional extras
  (`mongo` / `postgres`); SQLAlchemy + aiosqlite + Alembic in base deps.

## 0.1.1 — 2026-04-27

- Rewrite the README's relative links (`examples/minimal/`,
  `docs/security.md`, `LICENSE`, `SECURITY.md`, etc.) as absolute
  GitHub / Read the Docs URLs so they resolve on the PyPI project page,
  not just on GitHub. README-only release.

## 0.1.0 — 2026-04-27

First tagged release. Bundles M1–M6 from the development plan into a
single Apache-2.0 package on PyPI.

### M1 — skeleton

- `RegStack` façade, `RegStackConfig` (env + TOML loader), `BaseUser`,
  `UserRepo`, `BlacklistRepo`.
- JWT codec with per-purpose derived keys, per-token blacklist, bulk
  revocation via `tokens_invalidated_after`.
- Argon2 password hashing via `pwdlib`.
- JSON router: `register`, `login`, `logout`, `me`.
- Console email backend.
- `regstack init` wizard.
- `examples/minimal/` embedding demo.

### M2 — verification + reset

- Durable `pending_registrations` collection (hashed tokens, TTL).
- `verify`, `resend-verification`, `forgot-password`, `reset-password`.
- Login lockout (`LoginAttemptRepo` + `LockoutService` → 429 +
  `Retry-After`).
- SMTP backend (aiosmtplib) and SES backend (lazy aioboto3).
- `MailComposer` with Jinja2 `ChoiceLoader` for host-overridable email
  templates.

### M3 — account management + admin

- `PATCH /me`, `change-password`, `change-email` +
  `confirm-email-change`, `DELETE /account`.
- JSON admin router (`/admin/{stats,users,users/{id},users/{id}/resend-verification}`)
  behind `enable_admin_router`.
- `regstack create-admin` and `regstack doctor` CLIs.
- Float-precision JWT `iat` with `<=` bulk-revoke comparison so a login
  completing microseconds after a password / email change keeps its
  session.

### M4 — SSR pages + theming

- `ui_router` behind `enable_ui_router`: login, register, verify,
  forgot, reset, confirm-email-change, account dashboard.
- `core.css` + `theme.css` with CSS-custom-property theming, light +
  `prefers-color-scheme: dark`.
- Bundled `regstack.js` reads endpoints from `<body data-rs-api
  data-rs-ui>`.
- `theme_css_url` for stylesheet override; `add_template_dir` for full
  template overrides (shared with the email composer).
- CSP-friendly: no inline `<style>` or `style="…"`.

### M5 — SMS + optional 2FA

- `SmsService` ABC with `null` / `sns` / `twilio` backends.
- Phone routes (`/phone/start`, `/phone/confirm`, `DELETE /phone`).
- Two-step MFA login: `mfa_required` response on `/login` →
  `/login/mfa-confirm` with the SMS code.
- `MfaCodeRepo` with hashed 6-digit codes, attempt tracking, TTL on
  `expires_at`, unique on `(user_id, kind)`.
- SSR `mfa-confirm` page and "SMS two-factor authentication" section
  on `/account/me` (set up + disable).
- E.164 phone validation.

### M6 — docs + CI + release

- Sphinx documentation (markdown via myst-parser, Furo theme).
- Quickstart, configuration, architecture, security, embedding,
  theming, CLI, and API reference pages.
- GitHub Actions: parallel test matrix on push/PR; OIDC PyPI publish
  on `v*` tags.
- `CHANGELOG.md` and `SECURITY.md`.

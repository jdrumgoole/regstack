# Daily Security Review — Scheduled Agent Prompt

Copy this prompt into the scheduled agent at https://claude.ai/code/scheduled

---

You are a security reviewer for the **regstack** project — an embeddable
account-management library for FastAPI apps, published as the `regstack`
package on PyPI. It runs against three storage backends (SQLite,
PostgreSQL, MongoDB) selected by URL scheme, and ships an opt-in OAuth
subsystem (Google).

**This is a library, not a deployed service.** The threat model is:

- A host app that embeds regstack inherits its bugs.
- The PyPI wheel itself is a supply-chain artifact — anyone running
  `pip install regstack` runs the code in it.
- The published API surface is a stability commitment; breaking changes
  hurt every host.

Walk every module under `src/regstack/`. The full architectural notes
are in `CLAUDE.md` and `docs/architecture.md`; the threat model is in
`docs/security.md`; the OAuth design (security-relevant decisions and
their rationale) is in `tasks/oauth-design.md`.

## 1. Dependency Vulnerability Check

- Read `pyproject.toml` and `uv.lock` to identify all dependencies with
  exact versions, including the optional extras (`postgres`, `mongo`,
  `oauth`, `ses`, `sns`, `twilio`, `docs`, `dev`).
- Use WebSearch to check each major dependency for CVEs / advisories
  in the last 90 days. Search: "[package] CVE 2026", "[package]
  security advisory", "[package] vulnerability".
- Key packages: `fastapi`, `pydantic`, `pydantic-settings`, `sqlalchemy`,
  `alembic`, `aiosqlite`, `asyncpg`, `pymongo`, `pwdlib` (Argon2),
  `pyjwt[crypto]`, `cryptography`, `aiosmtplib`, `aioboto3`, `twilio`,
  `httpx`, `jinja2`, `python-multipart`, `dnspython`.
- Flag any dependency more than 2 major versions behind the latest
  release.
- Check whether any dependency has been yanked or compromised.
- **Optional-extra hygiene:** confirm that the extras don't pull in
  more than they declare. The `oauth` extra should add only
  `pyjwt[crypto]` (which transitively pulls `cryptography`); the
  `postgres` extra only `asyncpg`. Anything more is a supply-chain
  concern.

## 2. Hardcoded Secrets Scan

- Search the entire `src/`, `tests/`, `examples/`, and `docs/` trees
  (excluding `.venv/`, `node_modules/`) for:
  - API keys: `sk-`, `pk_`, `phc_`, `api_key = "`, `token = "`, `AKIA`
  - Connection strings: `mongodb://` (with credentials), `mongodb+srv://`,
    `postgres://` (with credentials)
  - Passwords: `password = "`, `passwd = "`, `secret = "` — exclude
    obvious test fixtures (`"hunter2hunter2"`, `"test-secret"`)
  - Private keys: `BEGIN RSA`, `BEGIN EC`, `BEGIN PRIVATE`
  - JWTs in source: `eyJ` followed by base64
- Check for sensitive files in git:
  `git ls-files | grep -iE '\.env|\.key|\.pem|secret|credential|\.p12|\.pfx'`.
  The only file that's expected to mention secrets is the **example
  template** `regstack.toml.example` (which references env-var names,
  not values).
- Verify `.gitignore` covers `.env`, `regstack.secrets.env`, `*.pem`,
  `*.key`, `*.p12`, `htmlcov/`, `.coverage`.
- Specifically check `examples/sqlite/`, `examples/postgres/`,
  `examples/mongo/` — each ships a `regstack.toml` for the demo. None
  should contain a real secret.

## 3. Authentication & Authorization Audit

- Read `src/regstack/app.py` and `src/regstack/routers/__init__.py`
  to enumerate every router conditionally mounted on the composite
  `router`.
- For EVERY endpoint in `src/regstack/routers/`:
  - Check that authenticated endpoints inject either
    `rs.deps.current_user()` or `rs.deps.current_admin()`.
  - Verify any endpoint that mutates user state requires authentication.
  - Flag any endpoint that returns user-visible data without going
    through the `UserPublic` projection (which excludes
    `hashed_password`).
- Verify the bulk-revoke discipline:
  - Successful **password reset** must call `users.update_password`
    AND `lockout.clear`.
  - Successful **password change** must update the hash and bump
    `tokens_invalidated_after`.
  - Successful **email-change confirm** must bump
    `tokens_invalidated_after`.
  - **Admin disable** (`PATCH /admin/users/{id} {is_active: false}`)
    must bump `tokens_invalidated_after`.
  - **Account deletion** must cascade `oauth_identities` first
    (anti-orphan), then delete the user.
- Verify that `BaseUser.hashed_password is None` (OAuth-only users) is
  handled correctly:
  - Login returns the same generic 401 a wrong-password attempt gets
    (no enumeration of OAuth-only accounts).
  - `change-password`, `change-email`, `delete-account` return 400
    pointing the user at the password-reset flow.
- Verify constant-time password comparison via `pwdlib` (not `==`).
- Verify the JWT signing keys are derived per-purpose
  (`derive_secret(jwt_secret, purpose)` in
  `src/regstack/config/secrets.py`). Compromise of one purpose key
  must not compromise the master.

## 4. Storage-layer Injection Safety

regstack has two backends; both need to be audited.

### 4a. MongoDB backend

- Search `src/regstack/backends/mongo/repositories/` for any place
  user input from request parameters, form data, or JSON body is
  interpolated directly into a MongoDB query filter.
- Check for unsafe operators that could be injected: `$where`,
  `$expr`, `$function`, `$accumulator`. The repos should never
  use these.
- Look for regex patterns built from user input — verify
  `re.escape()` is used.
- Verify `ObjectId` parsing of URL parameters is wrapped in
  `try/except` or guarded by `ObjectId.is_valid(...)` (e.g.
  `UserRepo.get_by_id`, `OAuthIdentityRepo.delete`).
- Check that `$in` queries don't accept unbounded arrays from user
  input.

### 4b. SQL backend (SQLite + Postgres)

- All queries in `src/regstack/backends/sql/repositories/` should use
  SQLAlchemy Core with parameterised binds — never f-string or `%`
  interpolation into raw SQL.
- Search for any `text(...)` usage; verify each call uses bind
  parameters and not interpolation.
- The `UtcDateTime` TypeDecorator (`src/regstack/backends/sql/types.py`)
  must consistently re-attach UTC tzinfo on read — a regression here
  silently corrupts timestamps.
- Check `purge_expired()` implementations across all SQL repos. They
  must use bound parameters for the cutoff timestamp.

## 5. Input Validation & Injection

- Every API input must go through a Pydantic model — flag any endpoint
  that accepts a raw `dict` or untyped JSON body.
- Verify `EmailStr` validation on all email-bearing fields.
- Verify `is_valid_e164()` is called on every phone-number input
  before storage.
- Check email-template rendering — the bundled `MailComposer`
  templates use Jinja2 with autoescape on for HTML. Any new email
  template that disables autoescape is a finding.
- Check SSR templates (`src/regstack/ui/templates/`) for `| safe`
  filters or `{% autoescape false %}` blocks. They should not exist.
- Check the `regstack init` CLI wizard for any input fed directly
  into a shell command — it shouldn't be doing this.
- Verify that `redirect_to` query parameters (in OAuth `start` /
  `link/start`) are validated same-origin against `config.base_url`.
  An off-site target must return 400.

## 6. Rate Limiting & DoS Prevention

- Login lockout: `LockoutService` should be wired on the login route,
  and `record_failure` must run before the password is verified so a
  locked-out attacker can't probe whether their guess was right.
- The `login_attempts` collection on Mongo has a TTL index sized to
  `login_lockout_window_seconds`; SQL backends rely on read-side
  filtering plus `purge_expired()`. Confirm both are still wired.
- All paginated admin endpoints (`/admin/users` listings) must cap
  `limit` (default + max). Search for any `limit:` parameter without
  an upper bound.
- OAuth state rows have a 5-minute TTL by default
  (`config.oauth.state_ttl_seconds`). Confirm the TTL still fires on
  Mongo (TTL index) and that SQL backends don't accumulate
  unbounded state rows.
- File upload endpoints — regstack doesn't have any user-facing
  uploads today. Flag if one was added.

## 7. Session & Token Security

- JWT configuration: HS256 by default, secret read from
  `config.jwt_secret`, ≥32 chars enforced at construction.
- Every JWT must include `jti`. Without it, per-token revocation via
  `BlacklistRepo` doesn't work.
- The bulk-revoke comparison is `payload.iat <= cutoff`. Verify
  `JwtCodec` still emits `iat` as a **float** (not `int`) so a login
  completing in the same wall-clock second as a password change
  isn't falsely revoked. CLAUDE.md "Bulk revocation: float iat and
  <= cutoff" section explains why.
- Check that `JwtCodec` still disables pyjwt's `exp`/`iat` checks
  and validates them against the injected `Clock`. Tests rely on
  this seam; production correctness depends on it too.
- Verify token revocation in `AuthDependencies._authenticate`:
  - Decode token → 401 on failure
  - `blacklist.is_revoked(jti)` → 401 if revoked
  - `is_payload_bulk_revoked(payload, user.tokens_invalidated_after)`
    → 401 if past the cutoff
  - User must be `is_active=True`
- Check for token leakage in logs (search for `log.info`/`log.error`
  near `token`, `access_token`, `id_token`).

## 8. CSP and the SSR Layer

- Check `src/regstack/ui/templates/` for any `<style>` block or
  `style="..."` attribute. The bundled templates are CSP-friendly
  (`style-src 'self'` works without `unsafe-inline`).
- The bundled `regstack.js` is loaded via `<script src defer>`. No
  inline `<script>` tags should exist in any template.
- Confirm the new `oauth_complete.html` template (added in 0.3.0)
  follows the same discipline.
- The SSR pages are stateless — they don't render auth state into
  HTML. Flag any template that includes `{{ user.email }}` or similar.

## 9. Data Exposure

- API responses must use `UserPublic`, never `BaseUser` directly.
  `UserPublic` excludes `hashed_password`,
  `tokens_invalidated_after`, internal IDs.
- Verify error messages don't leak internals:
  - Database driver errors → 500 with a generic message, not the
    raw exception.
  - JWT decode errors → 401 generic, never the specific reason.
  - OAuth callback failures → redirect to `/login?error=<code>` with
    a short code, never the underlying exception.
- Check for any `f"... {exc}"` patterns in HTTP error responses.
- Verify nothing logs the JWT secret, OAuth client_secret, or SMTP
  password.

## 10. Git History & Recent Changes

- Run `git log --oneline -30` to review recent commits.
- Check for any commits that might have introduced security issues —
  changes to `auth/`, `config/secrets.py`, the OAuth subsystem, the
  routers, or the JWT codec.
- Run `git log --all --oneline -10 --diff-filter=A -- '*.env' '*.key'
  '*.pem' '*secret*' '*credential*'` to check for accidentally
  committed secrets.
- Cross-reference recent CVEs (from §1) against the dependency
  versions touched by recent commits.

## 11. Third-Party Integration Security

regstack ships pluggable backends for email and SMS, plus the OAuth
provider abstraction. Each is a third-party touchpoint.

- **SMTP backend** (`aiosmtplib`): verify the password is loaded from
  `EmailConfig.smtp_password: SecretStr` and never echoed in logs or
  error messages.
- **SES backend** (`aioboto3`): verify the AWS profile / region come
  from config, never hardcoded.
- **SNS / Twilio SMS**: same — credentials from config, lazy import
  so a base install doesn't pull `aioboto3` / `twilio`.
- **Google OAuth**: verify the client_secret is stored as
  `SecretStr`, never echoed. The Google ID-token verification path
  must go through `PyJWKClient` against the configured `jwks_url`
  (default Google's), and the JWKS lookup must time out (the
  `urllib.request.urlopen(timeout=...)` path inside `PyJWKClient`).

## 12. GitHub Actions Workflow Security

Review every file in `.github/workflows/*.yml`. Confirm the list
against the live filesystem at review time — flag any new workflow
not enumerated here so the next reviewer picks it up.

### 12a. Third-party action pinning

- List every `uses:` directive. Flag any that reference a version
  tag (e.g. `actions/checkout@v4`) rather than a commit SHA. Tag
  references can be rewritten by the action author — SHA pinning
  defends against supply-chain swaps.
- Specifically check `astral-sh/setup-uv` and `pypa/gh-action-pypi-publish`.
  These run with access to the repo and `secrets.*`.

### 12b. Untrusted input injection

- Search for `${{ github.event.* }}` interpolations inside `run:`
  blocks. Anything from `pull_request.title`, `pull_request.body`,
  `issue.title`, `head_ref`, `comment.body` is attacker-controlled
  and must NOT be expanded inline into a shell command.
- Safe pattern: read via an env var, e.g.
  `env: { PR_TITLE: ${{ github.event.pull_request.title }} }` then
  use `$PR_TITLE` in the script.
- Reference: <https://securitylab.github.com/research/github-actions-untrusted-input/>

### 12c. Workflow permissions and GITHUB_TOKEN scope

- Every workflow / job should declare `permissions:`. Absence means
  default permissions, which are too broad.
- Look for `permissions: write-all` or jobs that grant
  `contents: write` / `pull-requests: write` without needing them.
- `publish.yml` is the highest-value target — verify its
  `permissions:` block is `id-token: write` and nothing else.

### 12d. `pull_request_target` trigger

- This trigger runs workflows in the context of the target repo with
  read/write secrets access and checks out the PR head by default —
  a known exploit vector. Flag any workflow using `pull_request_target`.
  If one exists, confirm it never checks out the untrusted PR head
  before validating authorship.

### 12e. Self-hosted runner hardening

- regstack uses GitHub-hosted runners only. Flag any
  `runs-on: [self-hosted, ...]` that appears.

### 12f. Secrets exposure

- List every `secrets.*` usage. The publish workflow uses OIDC
  trusted publishing, so there should be no PyPI token. If a
  `PYPI_API_TOKEN` secret reappears, that's a regression.
- Check for `echo "::add-mask::"` usage when secrets flow into
  outputs.
- Flag any `if: secrets.X != ''` patterns — those leak the existence
  of a secret.

### 12g. Cache poisoning

- Look for `actions/cache` usage that keys on user-controlled input
  (e.g. branch name). A poisoned cache can inject files into
  subsequent runs.

### 12h. Deploy authorisation

- `publish.yml` pushes to PyPI on tag push. Verify the trigger is
  `on: { push: { tags: [...] } }` and that only maintainers can push
  tags (branch/tag protection on `v*`).

## 13. PyPI Package Integrity

regstack publishes as `regstack` on PyPI via
`.github/workflows/publish.yml`. **This is the highest-impact section
of the review** — anything bad in the wheel runs on every consumer's
machine.

### 13a. Sdist/wheel contents

- Read `pyproject.toml` `[tool.hatch]` / `[tool.hatch.build.targets.wheel]`
  sections to understand what files ship in the package.
- Run `uv build` locally into a temp dir and list the sdist + wheel
  contents (`unzip -l dist/*.whl`). Verify NONE of these end up in
  the package:
  - `tests/` (regstack does NOT include tests in the wheel)
  - `docs/`, `htmlcov/`, `.coverage`, `.pytest_cache/`, `.ruff_cache/`,
    `.mypy_cache/`, `.venv/`
  - `tasks/` (project planning — design docs may be useful for
    transparency but should not bundle into the wheel; check
    pyproject's include list)
  - `tasks.py` (invoke build commands — host has no use for it)
  - `examples/` (demos; not part of the published API)
  - `.github/`, `.git/`, `.gitignore`
  - Any `.DS_Store`, `.idea/`, `.vscode/`
- Flag any file that looks like an artifact, screenshot, or secret
  bleeding into the wheel.

### 13b. Hardcoded environment leaks

- Grep the built wheel for `localhost`, `127.0.0.1`, sample emails
  from the test suite (e.g. `alice@example.com`, `hunter2hunter2`).
  These are fine in tests but should NOT appear in the wheel
  contents (they imply a test fixture leaked).
- Verify the wheel does not embed any developer-machine paths
  (`/Users/`, `/home/`).

### 13c. Dependency pinning in shipped metadata

- Read the `dependencies = [...]` block in `pyproject.toml`. Each
  entry must have a lower bound for known-CVE-fixed versions. Flag
  any dependency pinned to a version with an open advisory
  (cross-reference with §1).
- Check that optional / dev-only deps are NOT in the main
  `dependencies` list — they'd be forced on every downstream
  consumer.
- Specifically: `pymongo`, `asyncpg`, `aioboto3`, `twilio`,
  `pyjwt[crypto]` should be in optional-dependencies, NOT in the
  base `dependencies`. The 0.2.1 release shipped broken because of
  this exact pattern; a regression test exists at
  `tests/unit/test_base_install_imports.py` — verify it still passes
  in CI.

### 13d. Publication path

- `publish.yml` uses PyPI Trusted Publishing via OpenID Connect
  (`pypa/gh-action-pypi-publish` without an API token input), not a
  long-lived `PYPI_API_TOKEN` secret. Token-based publishing is a
  standing theft risk.
- Confirm the publishing job has `permissions: { id-token: write }`
  and nothing else.
- Verify the workflow is triggered by tag push (`v*`), not branch
  push, and that the tag pattern matches the release naming.
- Verify the "verify tag matches version" step reads version from
  `pyproject.toml` (via `tomllib`), NOT by importing
  `regstack.version` — the latter would re-introduce the 0.2.0 bug
  where a base install couldn't import the package.

### 13e. Supply-chain bill of materials

- Check whether releases are signed with sigstore via
  `pypa/gh-action-pypi-publish` `attestations: true`. If not signed,
  note it as INFO.

### 13f. Installed package surface

- Verify what `pip install regstack` actually lets a consumer
  execute:
  - `[project.scripts]` lists `regstack = "regstack.cli.__main__:main"`.
    Confirm the CLI subcommands (init, doctor, create-admin,
    migrate) don't default to a production database or hostname.
  - regstack uses PEP 621 / `pyproject.toml`, so there's no
    `setup.py` post-install hook concern.

### 13g. Version history review

- `pip index versions regstack` — any yanked versions? Note them.
- Compare the latest PyPI version against
  `src/regstack/version.py:__version__` and `pyproject.toml`
  `version`. A mismatch in git is harmless but flag if PyPI has a
  higher version than git's tag (would imply an out-of-band publish).

## 14. OAuth Subsystem Audit

OAuth is regstack's most security-sensitive surface. The full design
+ threat model is in `tasks/oauth-design.md`. Walk
`src/regstack/oauth/` and `src/regstack/routers/oauth.py` against it.

### 14a. PKCE invariants

- The `code_verifier` is generated server-side and stored on the
  `oauth_states` row. It must NEVER appear in the URL the browser
  sees — only the `code_challenge` (its SHA-256) goes to the
  provider as a query parameter.
- Confirm `_begin_flow` in `routers/oauth.py` puts the verifier on
  the row, not in the redirect.

### 14b. State row lifecycle

- The OAuth `state` parameter the browser carries is the random
  `id` of an `oauth_states` row.
- The callback must reject:
  - Missing row (`?error=bad_state`).
  - Expired row (`?error=state_expired`) — verify the comparison
    uses `rs.clock.now()`, NOT `datetime.now(UTC)`. A clock-injection
    drift here would mask FrozenClock-driven test failures.
- After the callback succeeds, the row's `result_token` slot is
  populated. The `/oauth/exchange` endpoint must consume the row
  atomically (read + delete in one transaction) so a second exchange
  with the same id returns 404.

### 14c. ID-token verification

- `GoogleProvider.verify_id_token` must validate signature against
  Google's JWKS, `iss` matches the configured issuer, `aud` matches
  the configured `client_id`, `exp > now`, and `nonce` matches the
  expected value.
- Any failure must raise `OAuthIdTokenError` with a generic outer
  message; the specific check that failed is logged but never
  echoed to the redirect.

### 14d. Linking-policy enforcement

- Default behaviour: a Google sign-in for an existing email-
  registered user returns `?error=email_in_use`. Auto-link only
  fires when `oauth.auto_link_verified_emails=True` AND the ID
  token's `email_verified=true`.
- An identity already linked to a different user must return
  `?error=identity_in_use`. Re-linking the same identity to the
  same user must return `?error=already_linked`. Both protect
  against silent ownership transfer.

### 14e. OAuth-only user side effects

- `POST /login` rejects password attempts on `hashed_password=None`
  users with the same generic 401 a wrong-password attempt gets.
- `POST /change-password`, `POST /change-email`, `DELETE /account`
  return 400 with a forgot-password pointer for OAuth-only users.
- `DELETE /oauth/{provider}/link` returns 400
  (`last sign-in method`) when the user has no password set and
  only one linked provider.

### 14f. Identity-row uniqueness

- `(provider, subject_id)` uniqueness must be enforced at the
  database level (Mongo unique index, SQL `UniqueConstraint`).
  A regression here would let two regstack users share one Google
  account.
- `(user_id, provider)` uniqueness is the same — one Google
  identity per regstack user.

### 14g. Hooks fire correctly

- Confirm `oauth_signin_started`, `oauth_signin_completed`,
  `oauth_account_linked`, `oauth_account_unlinked` are still in
  `KNOWN_EVENTS` and that the router fires them at the right
  points.

## 15. Multi-backend Parity & Schema Migration Safety

regstack's whole abstraction story is "the same security properties
hold across SQLite + Postgres + Mongo". A drift between backends is
a security finding, not just a correctness one.

### 15a. CI matrix discipline

- `.github/workflows/test.yml` runs the parametrized suite against
  all three backends. Confirm the test matrix still covers all
  three. CLAUDE.md "A test run that doesn't cover every backend is
  a failing test" lays out the rule.
- `inv test-all` must be the release gate. Flag if any release was
  cut without it.

### 15b. Schema migration safety (SQL backends)

- Read `src/regstack/backends/sql/migrations/versions/`. Each
  migration must have a working `upgrade()` AND `downgrade()`.
- The latest migration that flipped `users.hashed_password` to
  nullable (`0002_oauth.py`) used `batch_alter_table` for SQLite
  compatibility. Verify any new migration that alters columns
  follows the same pattern.
- The autogen-drift test (`tests/integration/test_sql_migrations.py`)
  catches `schema.py` ↔ migration mismatches. Confirm it's still
  green.

### 15c. Mongo index parity

- `src/regstack/backends/mongo/indexes.py` declares the Mongo
  indexes. Each unique constraint in `src/regstack/backends/sql/schema.py`
  must have a matching unique index on the Mongo side. Specifically:
  - `users.email` UNIQUE → `users.email_unique`.
  - `oauth_identities (provider, subject_id)` UNIQUE → matching
    Mongo index.
  - `oauth_identities (user_id, provider)` UNIQUE → matching index.
- TTL handling parity: every SQL table with an `expires_at` column
  must have either a Mongo TTL index OR a documented
  `purge_expired()` reaper that the host is expected to call. SQL
  backends rely on the latter — verify the comment in each repo's
  `purge_expired` still says so.

### 15d. Datetime tz-awareness

- `make_client(tz_aware=True)` for Mongo, `UtcDateTime` TypeDecorator
  for SQL. A regression to naive datetimes would silently break the
  bulk-revoke comparison (`iat < tokens_invalidated_after` raises
  `TypeError` on a tz mismatch).

## Report & PR

Write your full report to `docs/security-reports/YYYY-MM-DD.md`
(using today's date).

Summarise ALL findings using this structure:

### 🔴 CRITICAL (Immediate action required)
Issues that represent active security vulnerabilities or data
exposure risks.

### 🟠 WARNING (Address within 1 week)
Issues that could become vulnerabilities or represent defence-in-
depth gaps.

### 🟡 INFO (Best practice recommendations)
Suggestions to improve security posture.

### 🟢 CLEAN (Passed review)
Areas that were reviewed and found to be secure.

### 📊 Summary
- Date of review.
- regstack version reviewed (from `src/regstack/version.py`).
- Total issues found by severity.
- Comparison with previous review (if context available).
- Top 3 priorities for the development team.

If the codebase is clean, report a clean bill of health with the date
and what was checked.

## File a PR

After writing the report:

1. Create a branch named `security-review/YYYY-MM-DD`.
2. Commit the report file to the branch.
3. Open a PR with:
   - Title: `Security Review — YYYY-MM-DD`
   - Body: A short summary of findings (number of critical / warning
     / info issues, or "Clean bill of health").
   - Severity tag in the title:
     - CRITICAL findings: prepend `[security-critical]` to the title.
     - WARNING findings: prepend `[security-warning]` to the title.
     - Clean: prepend `[security-clean]` to the title.

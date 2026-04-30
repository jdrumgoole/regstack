# Security model

This page describes the threats regstack defends against and how. Each
section calls out where regstack defers to the host (TLS, CSP headers,
backups) so you know what is and isn't your responsibility.

The summary: regstack tries to make the boring 80% of auth security
correct by default, with no flags to turn off the defaults. Where there
are tradeoffs, they're documented here rather than buried in code.

## Passwords

- **Hashing.** Argon2id with library defaults.
  `PasswordHasher.needs_rehash(...)` is available so a future
  parameter bump can quietly upgrade existing hashes on a user's next
  successful login.
- **Length.** Minimum 8, maximum 128 (UTF-8). Validated by the
  pydantic input model on every create / change endpoint.
- **Storage.** Plaintext is never logged or returned. The
  `BaseUser.hashed_password` field is excluded from the `UserPublic`
  serialization model that the API returns.

## JWT issuance and validation

regstack uses [JWTs (RFC 7519)](https://datatracker.ietf.org/doc/html/rfc7519)
for authentication. A JWT is a signed, self-contained credential —
the server doesn't have to remember it to validate it. That's why
revocation needs explicit handling (see below).

- **Per-purpose signing keys.** A single master `config.jwt_secret`
  (≥32 chars) is fed through HMAC-SHA256 with a per-purpose label to
  derive a separate signing key for each token kind: `session`,
  `password_reset`, `email_change`, `phone_setup`, `login_mfa`.
  Compromise of one derived key does **not** compromise the master.
- **`iat` is a float.** RFC 7519 explicitly allows fractional
  seconds. We use them. This matters for the bulk-revoke comparison
  (see below) — without sub-second precision, a login completing in
  the same second as a password change would be wrongly revoked.
- **`exp` is enforced by regstack**, using the injected `Clock`,
  rather than relying on pyjwt's wall-clock check. This keeps
  `FrozenClock`-driven tests consistent and makes the cutoff
  comparison deterministic.
- **`purpose` is required.** Decode requires the `purpose` claim to
  match the expected purpose. Trying to use a session token where a
  password-reset token is expected fails at the JWT layer — well
  before any business logic.
- **`aud`** is validated when `config.jwt_audience` is set.

## Revocation: two complementary mechanisms

A JWT can't be "logged out" the way a session cookie can — the server
doesn't store it. To make logout and password change actually
invalidate tokens, regstack runs both checks on every authenticated
request:

1. **Per-token blacklist.** `BlacklistRepo` stores `{jti, exp}` rows.
   `POST /logout` inserts one; the auth dependency rejects any token
   whose `jti` is present. Mongo gets free expiry via a TTL index on
   `exp`; SQL backends rely on read-side filtering plus the optional
   `purge_expired()` reaper.
2. **Bulk revocation.** `User.tokens_invalidated_after` is a
   timestamp. The check is `payload.iat <= cutoff`: tokens issued at
   or before the cutoff are revoked. A login completing microseconds
   *after* a password / email change has `iat > cutoff` (float `iat`
   makes the comparison precise) and survives.

Bulk revoke fires on:

- successful password reset
- successful change-password
- successful change-email confirm
- admin-disabled user (`PATCH /admin/users/{id} {is_active: false}`)

## Account enumeration

Account enumeration is when an attacker can tell whether a given
email is registered by observing how a public endpoint responds. It
turns "guess passwords for a known account" into "harvest the
customer list".

regstack returns an identical response for "user exists" and "user does
not" on the routes most useful for probing:

- `POST /forgot-password` → always 202 with the same body.
- `POST /resend-verification` → always 202 with the same body.

`POST /register` does return 409 on a duplicate email, since the UX
benefit ("did you mean to log in?") outweighs the enumeration concern
for a route that's already rate-limited by the lockout subsystem and
visible to logged-out users only.

## Login lockout

- `LoginAttemptRepo` stores one row per failed login `{email, when, ip}`.
- On Mongo the collection has a TTL index whose `expireAfterSeconds`
  matches `login_lockout_window_seconds` — old failures reap
  themselves. SQL backends apply read-side window filtering.
- `LockoutService.check(email)` returns `locked=true` once the count
  of failures-in-window exceeds `login_lockout_threshold`.
- A locked login returns HTTP 429 with a `Retry-After` header
  *before* the password is verified, so a locked-out attacker can't
  even tell whether their next guess was correct.
- Successful login calls `lockout.clear(email)` to wipe accumulated
  failures.
- Disabled in tests via `rate_limit_disabled=True`.

## Email verification (durable, hashed token)

- Random 32-byte URL-safe token, SHA-256 hashed in
  `pending_registrations.token_hash`. The raw token only ever exists
  in the email body and the click URL — a database backup can't be
  replayed.
- Mongo: TTL index on `expires_at` reaps unused pending rows
  automatically. SQL backends rely on read-side `expires_at > now()`
  filtering (so stale rows are harmless) plus the optional
  `backend.pending.purge_expired()` reaper for disk hygiene.
- Re-issuing a code (`POST /resend-verification`) atomically replaces
  the row, so the previous link silently stops working.
- Pending rows are deleted on successful verification.

## Password reset

- 30-minute JWT (purpose `password_reset`) carrying `sub=user_id`.
- The endpoint is anti-enumeration: 202 regardless of whether the
  email exists. The reset email is sent only if the address resolves
  to an active user.
- On confirmation, regstack: (a) updates the password hash, (b) bumps
  `tokens_invalidated_after` to revoke every outstanding session, (c)
  clears the lockout for the user's email so they aren't still gated
  out.

## Email change (re-auth + re-verify)

- Requires the current password.
- 409 if the new email already belongs to another user.
- 1-hour JWT (purpose `email_change`) carries `sub=user_id` and a
  `new_email` custom claim.
- The confirmation token is sent to the **new** address, not the old
  one — so a typo'd new email simply fails to deliver instead of
  silently locking the user out of their own account.
- Confirm swaps the email atomically (DB-level unique constraint on
  `users.email`), bumps `tokens_invalidated_after`, and clears the
  lockout for the previous address.

## SMS 2FA

- Codes are 6 digits, generated with `secrets.randbelow`, hashed in
  `mfa_codes.code_hash` with a TTL of 5 minutes.
- Per-user-per-kind unique so re-issuing a code overwrites the old
  one; prior SMS messages stop working.
- Each row has an `attempts` counter; after `sms_code_max_attempts`
  wrong guesses the row is deleted (forces a re-issue) and the
  response is `LOCKED`.
- Phone setup requires the current password before the code is sent.
- Phone disable requires the current password (no SMS round trip — an
  attacker would need both the live session and the current password).
- Phone numbers are validated as E.164 (the international phone
  number standard, e.g. `+15551234567`).
- Login MFA: when `user.is_mfa_enabled and user.phone_number`, the
  password-correct path issues a short-lived `mfa_pending` JWT
  instead of a session token, sends an SMS, and requires
  `POST /login/mfa-confirm` to complete.

## OAuth (Sign in with Google)

Opt-in subsystem behind `enable_oauth` and the `oauth` extra. Five
JSON endpoints plus an SSR token-handoff page. The full host-facing
guide is in [OAuth](oauth.md); this section is the threat model.

- **Server-side PKCE.** The `code_verifier` is generated server-side
  and persisted on a `oauth_states` row; only its SHA-256
  `code_challenge` ever travels through the browser. The token
  exchange POSTs the verifier directly from the regstack server to
  Google's token endpoint, so a leaked browser-side state value
  alone can't drive a token exchange.
- **State row is the OAuth `state` parameter.** Random 32-byte
  url-safe id; carries `code_verifier`, `nonce`, `redirect_to`,
  `mode` (`signin` or `link`), optional `linking_user_id`. The
  callback looks the row up by id, rejects missing / expired rows
  with `?error=bad_state` or `?error=state_expired`. Mongo gets free
  TTL via `expireAfterSeconds`; SQL backends rely on read-side
  `expires_at > now()` plus `purge_expired()`.
- **ID token verification.** Signature against Google's JWKS
  (`PyJWKClient` cached), `iss` matches Google, `aud` matches the
  configured `client_id`, `exp > now`, `nonce` matches the value
  stashed on the state row. Any failure raises
  `OAuthIdTokenError` and the callback redirects to the login page
  with `?error=id_token_failed` — the specific check that failed is
  logged but not echoed.
- **Account-linking policy.** Defaults to **refuse**. If a Google
  sign-in carries an email already owned by a regstack user, the
  callback returns `?error=email_in_use` and the user has to sign
  in with their existing password before linking from
  `/account/me`. Auto-linking is available behind
  `oauth.auto_link_verified_emails = true`; even then, regstack
  requires `email_verified=true` on the ID token. The threat
  auto-link accepts is *email recycling at the provider* — if
  someone later acquires the original Gmail address, they could
  sign in as the original regstack user. Hosts choosing auto-link
  do so eyes-open. Full writeup in
  `tasks/oauth-design.md` § 1.
- **One-time token-handoff.** After a successful callback, the
  fresh session JWT is stashed on the `oauth_states.result_token`
  field and the SPA exchanges its state-id for the token via
  `POST /oauth/exchange`. The exchange consumes the row atomically
  (read + delete in one transaction); a second exchange call with
  the same id returns 404. Tokens never appear in URLs longer than
  the callback redirect, no cookies are set.
- **OAuth-issued sessions are normal session JWTs** signed with the
  same `session`-purpose key. The `tokens_invalidated_after` bulk-
  revoke applies — a password change or admin-disable kills any
  OAuth-issued session too.
- **Open-redirect protection.** `redirect_to` on `/start` is
  validated same-origin against `config.base_url`; a request with
  an off-site target returns 400.
- **Identity-row uniqueness.** `(provider, subject_id)` is unique
  so two regstack users can't share one external account; a
  second-user link attempt returns `?error=identity_in_use`.
  `(user_id, provider)` is also unique so re-linking the same
  provider to the same user returns `?error=already_linked`
  rather than silently succeeding.
- **OAuth-only users.** A Google sign-up creates a user with
  `hashed_password=None`. Login with a password against such an
  account returns the same generic 401 a wrong-password attempt
  gets — never reveal that an account exists but has no password
  set, so an attacker can't enumerate which accounts to phish via
  OAuth. `change-password` / `change-email` / `delete-account`
  return 400 with a pointer at the password-reset flow, which
  doubles as a "set initial password" path.
- **Refuse to unlink the only auth method.**
  `DELETE /oauth/{provider}/link` returns 400 if the user has no
  password and only the one identity. Forces them to either set a
  password (via reset) or link another provider first.

## CSP and the SSR layer

Content Security Policy (CSP) is a browser feature that restricts
what sources of scripts and styles a page can load. Inline `<style>`
and `<script>` blocks force you to either allow `unsafe-inline`
(which defeats most of CSP) or skip the header entirely. regstack
avoids that:

- The bundled templates contain **no inline `<style>` blocks** and
  **no `style="..."` attributes**. CSS is loaded only via `<link>`
  tags from `core.css`, the bundled `theme.css`, and the optional
  host `theme_css_url`. A `style-src 'self' <host-theme-domain>`
  policy works without `unsafe-inline`.
- The bundled `regstack.js` is loaded via `<script src defer>` from
  the same static mount. A host CSP can add the static origin to
  `script-src` without `unsafe-inline`.
- The SSR pages are stateless — they read endpoint URLs from `<body
  data-rs-api data-rs-ui>` rather than baking them into the JS — so
  changing prefixes doesn't require shipping new JS.
- Auth state is in `localStorage` under `regstack.access_token`. The
  pending-MFA token uses `sessionStorage` so it doesn't survive a tab
  close. **No cookies** are set, which sidesteps CSRF concerns at the
  cost of XSS being more impactful — hosts that need cookie-based
  sessions can swap the JS at the same data attributes.

## What you still own as a host

- **TLS termination.** regstack assumes its endpoints are reachable
  only over HTTPS in production.
- **Reverse-proxy header trust.** `behind_proxy=True` is
  informational; the host configures the actual middleware (e.g.
  Starlette's `ProxyHeadersMiddleware`).
- **Content Security Policy headers.** regstack's SSR layer is
  CSP-friendly but the host emits the `Content-Security-Policy`
  response header.
- **Rate-limiting beyond the per-account login lockout.** A future
  milestone may add a `slowapi`-style middleware; for now host-level
  rate limits are the right place to push back broad attack traffic.
- **Backups, MongoDB user permissions, network-level isolation** between
  the app and the database.

## Reporting vulnerabilities

See `SECURITY.md` at the repository root.

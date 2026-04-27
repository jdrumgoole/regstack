# Security model

This is the threat model and the levers regstack provides against each
class of attack. Where regstack defers to the host (e.g. CSRF
middleware, transport security), that's called out explicitly.

## Passwords

- Hashing: Argon2id via [`pwdlib`](https://pypi.org/project/pwdlib/)
  with default parameters. `PasswordHasher.needs_rehash(...)` is
  available so a future parameter bump can quietly upgrade hashes on
  next successful login.
- Minimum length 8, maximum 128 (UTF-8). Passwords are validated by the
  pydantic input model on every create / change endpoint.
- Plaintext is never logged or returned. The `BaseUser.hashed_password`
  field is excluded from `UserPublic`.

## JWT issuance and validation

- One signing secret (`config.jwt_secret`, ≥32 chars) is fed through
  HMAC-SHA256 with a per-purpose label to derive a separate signing key
  for each token kind: `session`, `password_reset`, `email_change`,
  `phone_setup`, `login_mfa`. Compromise of one derived key does **not**
  compromise the master.
- `iat` is emitted as a float (RFC 7519 NumericDate explicitly allows
  this). This matters for the bulk-revoke comparison — see below.
- `exp` is enforced by regstack with the injected `Clock`, **not** by
  pyjwt's wall-clock check. This keeps `FrozenClock`-driven tests
  consistent and keeps the cutoff comparison precise.
- Decode requires the `purpose` claim to match the expected purpose.
  Trying to use a session token where a reset token is expected fails.
- `aud` is validated when `config.jwt_audience` is set.

## Revocation: two complementary mechanisms

Both are checked on every authenticated request in
`AuthDependencies._authenticate`:

1. **Per-token blacklist** — `BlacklistRepo` stores `{jti, exp}`.
   `POST /logout` inserts a row; the dependency rejects any token whose
   `jti` is present. A TTL index on `exp` reaps rows automatically once
   the token would have expired anyway.
2. **Bulk revocation** — `User.tokens_invalidated_after` is a timestamp.
   The check is `payload.iat <= cutoff`: tokens issued at or before the
   cutoff are revoked. A login completing microseconds **after** a
   password / email change has `iat > cutoff` (float `iat` makes the
   comparison precise) and survives.

Bulk revoke fires on:
- successful password reset
- successful change-password
- successful change-email confirm
- admin-disabled user (`PATCH /admin/users/{id} {is_active: false}`)

## Account enumeration

regstack returns the same response for "user exists" and "user does
not" on the routes most useful for probing:

- `POST /forgot-password` → always 202 with the same body.
- `POST /resend-verification` → always 202 with the same body.

`POST /register` does return 409 on a duplicate email, since the UX
benefit ("did you mean to log in?") outweighs the enumeration concern
for a route that's rate-limited by the lockout subsystem and visible to
a logged-out user only.

## Login lockout

- `LoginAttemptRepo` stores one row per failed login `{email, when, ip}`.
- The collection has a TTL index whose `expireAfterSeconds` matches
  `login_lockout_window_seconds` — Mongo reaps old failures
  automatically.
- `LockoutService.check(email)` returns `locked=true` once the count of
  failures-in-window exceeds `login_lockout_threshold`.
- A locked login returns 429 + `Retry-After` *before* the password is
  verified, so a locked-out attacker can't even tell whether their
  guess was correct.
- Successful login calls `lockout.clear(email)` to wipe accumulated
  failures eagerly.
- Disabled in tests via `rate_limit_disabled=True`.

## Email verification (durable, hashed token)

- Random 32-byte URL-safe token, SHA-256 hashed in
  `pending_registrations.token_hash`. The raw token only ever exists
  in the email body and the click URL.
- TTL index on `expires_at` reaps unused pending rows.
- Re-issuing a code (`POST /resend-verification`) `find_one_and_replace`s
  the row, so the previous link silently stops working.
- Pending rows are deleted on successful verification.

## Password reset

- 30-minute JWT (purpose `password_reset`) carrying `sub=user_id`.
- The endpoint is anti-enumeration: 202 regardless of email existence,
  email is only sent if the address resolves to an active user.
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
- Per-user-per-kind unique so re-issuing a code overwrites the old one;
  prior SMS messages stop working.
- Each row has an `attempts` counter; after `sms_code_max_attempts`
  wrong guesses the row is deleted (forces a re-issue) and the response
  is `LOCKED`.
- Phone setup requires the current password before the code is sent.
- Phone disable requires the current password (no SMS round trip — an
  attacker would need both the live session and the current password).
- Phone numbers are validated as E.164.
- Login MFA: when `user.is_mfa_enabled and user.phone_number`, the
  password-correct path issues a short-lived `mfa_pending` JWT instead
  of a session token, sends an SMS, and requires `POST
  /login/mfa-confirm` to complete.

## CSP and the SSR layer

- The bundled templates contain **no inline `<style>` blocks** and **no
  `style="..."` attributes**. CSS is loaded only via `<link>` tags from
  `core.css`, the bundled `theme.css`, and the optional host
  `theme_css_url`. A `style-src 'self' <host-theme-domain>` policy
  works without `unsafe-inline`.
- The bundled `regstack.js` is loaded via `<script src defer>` from the
  same static mount. A host CSP can add the static origin to
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

- TLS termination. regstack assumes its endpoints are reachable only
  over HTTPS in production.
- Reverse-proxy header trust (`behind_proxy=True` is informational; the
  host configures the actual middleware).
- Content Security Policy headers — regstack's SSR layer is
  CSP-friendly but the host emits the `Content-Security-Policy`
  response header.
- Rate-limiting beyond the per-account login lockout. A future
  milestone may add a `slowapi`-style middleware; for now host-level
  rate limits are the right place to push back broad attack traffic.
- Backups, MongoDB user permissions, network-level isolation between
  the app and the database.

## Reporting vulnerabilities

See `SECURITY.md` at the repository root.

# Design: Google OAuth (and a path to other providers)

Status: **proposal** — needs sign-off before code lands.
Target version: **0.3.0** (minor bump — new public surface).

## Goal

Let a regstack-using app add "Sign in with Google" without forcing
the host to write OAuth code, and without compromising the security
properties regstack already provides (per-token revocation, bulk
revoke, lockout, anti-enumeration).

Concretely the host should be able to:

```toml
# regstack.toml
enable_oauth = true

[oauth]
google_client_id = "12345.apps.googleusercontent.com"
# google_client_secret in regstack.secrets.env
```

…and get four working endpoints
(`GET /oauth/google/start`, `GET /oauth/google/callback`,
`POST /oauth/google/link/start`, `DELETE /oauth/google/link`),
a "Sign in with Google" button on the SSR login page, and a
"Connected accounts" section on `/account/me` — without writing any
OAuth code.

## Non-goals (for v1 of this feature)

- **GitHub / Microsoft / Apple.** The abstraction must accommodate
  them, but we ship Google only.
- **Cookie-based sessions.** OAuth does not change the existing
  bearer-token transport. The callback returns a JWT exactly the
  way `/login` does.
- **Custom OAuth providers (host-supplied OIDC issuers).** Possible
  later via the `OAuthProvider` ABC, not exposed in v1 config.
- **Connecting OAuth to admin role assignment.** OAuth-created users
  get default privileges; admins are still bootstrapped via
  `regstack create-admin`.

---

## The three load-bearing decisions

### 1. Account-linking policy (security, default)

**The problem.** A new Google sign-in arrives carrying email
`alice@gmail.com` with `email_verified=true`. There is already a
regstack user with that email, who registered with a password. Do
we (a) automatically link them and sign in, (b) refuse to link and
require explicit linking from settings, or (c) create a brand-new
account?

**Three options:**

| Option | Behaviour | Pro | Con |
|---|---|---|---|
| **A: auto-link** | Match by email, link silently, sign in. | Best UX. What most consumer apps do. | Trusts Google's `email_verified` claim *forever*. If Mallory acquires `alice@gmail.com` (Gmail recycling, ownership transfer, account compromise), Mallory can sign in as Alice on regstack — **even though Alice never linked her Google account to regstack**. |
| **B: log-in-then-link** | Refuse to link. Caller must sign in with the existing password first, then link from settings. | Preserves the invariant that you only get into an existing account by demonstrating control of its password (or an already-linked OAuth identity). Matches GitHub's stance. | More UX friction; user might give up. |
| **C: always create new** | Don't link. Make a second account. | Trivially safe. | Awful UX. Customer service nightmare. |

**Decision: B as the default. A is available behind a config flag**
(`oauth_auto_link_verified_emails: bool = False`).

The config flag's docstring will explicitly call out the
email-recycling threat so hosts choosing A are choosing eyes-open.
This is the same shape as `enable_admin_router` — opt-in, with the
risk documented at the flag.

### 2. Identity storage

**The problem.** A regstack user might be linked to one or more
external identities (Google now; GitHub later). We need a stable
key that survives email changes on the provider side.

**Decision.** Separate `oauth_identities` table/collection keyed by
`(provider, subject_id)`. *Not* embedded on `BaseUser` — backend-
specific embedded-document handling is exactly the mess we just
cleaned up with `count_unexpired`.

```
oauth_identities
  id            (uuid hex / ObjectId)
  user_id       (FK → users)
  provider      ("google" | future …)
  subject_id    (provider's stable user id; for Google: the `sub` claim)
  email         (snapshot at link time, non-authoritative)
  linked_at     (datetime UTC)
  last_used_at  (datetime UTC, nullable)

  UNIQUE (provider, subject_id)   -- one regstack user per provider identity
  UNIQUE (user_id, provider)      -- one provider identity per regstack user
```

Why `subject_id`, not email: providers' docs explicitly tell you the
email can change but the subject is stable. (Google's `sub` is
stable forever; GitHub's user-id is stable; Microsoft's `oid` is
stable.)

Why both unique constraints: the first stops two regstack users
from sharing one Gmail; the second stops one regstack user from
linking two Gmail accounts (which would confuse the UI for no
benefit).

### 3. State + PKCE storage

**The problem.** Between the redirect to Google and the callback we
need to remember a `code_verifier` (PKCE pre-image), the
`redirect_to` target, the mode (`signin` vs `link`), and possibly
the `linking_user_id` if this is an authenticated link flow.
Storing all of that in the OAuth `state` parameter (a signed JWT in
the URL) is tempting because it's stateless — but the
`code_verifier` is supposed to be a server-only secret, and putting
it in the browser URL defeats PKCE's threat model.

**Decision.** Server-side `oauth_states` row, addressed by random
ID; the OAuth `state` parameter carries only that ID.

```
oauth_states
  id              (random url-safe 32 bytes)
  code_verifier   (text)
  redirect_to     (text, validated against base_url's origin)
  mode            ("signin" | "link")
  linking_user_id (text, nullable — set when mode = "link")
  expires_at      (datetime UTC)
  result_token    (text, nullable — populated on callback success)

  UNIQUE (id)
  TTL on expires_at (Mongo) / read-side filter + purge_expired (SQL)
```

The `result_token` slot is how we hand the session JWT back to the
SPA without putting a token in a URL — see [Token handoff](#token-handoff)
below.

---

## The flow, end to end

### Sign-in (unauthenticated)

```
Browser → GET /api/auth/oauth/google/start?redirect_to=/account/me
          ─────────────────────────────────────────────────────────
regstack:
  - validate redirect_to is same-origin (rejects open-redirect attempts)
  - generate code_verifier (32 random bytes), code_challenge = SHA256(code_verifier) base64url
  - generate state_id = secrets.token_urlsafe(32)
  - insert oauth_states row { id=state_id, code_verifier, redirect_to,
                              mode="signin", expires_at=now+5min }
  - 302 → https://accounts.google.com/o/oauth2/v2/auth?
              response_type=code
              &client_id=…
              &redirect_uri={base_url}/api/auth/oauth/google/callback
              &scope=openid email profile
              &code_challenge={code_challenge}
              &code_challenge_method=S256
              &state={state_id}
              &nonce={signed_nonce}

User signs in to Google, grants consent.

Browser ← 302 → GET /api/auth/oauth/google/callback?code=…&state={state_id}
                ───────────────────────────────────────────────────────
regstack:
  - look up oauth_states row by state_id; reject if missing/expired
  - POST to https://oauth2.googleapis.com/token with code + code_verifier
  - verify ID token (signature against Google JWKS, iss, aud, exp, nonce)
  - extract { sub, email, email_verified, name, picture } from claims
  - resolve the user:
      identity = oauth_identities.find_by(provider="google", subject_id=sub)
      if identity: → user = users.get_by_id(identity.user_id)         [existing-link sign-in]
      else if existing_user_with_email and config.auto_link_verified_emails
              and email_verified:
        → insert oauth_identities row, user = existing_user           [auto-link]
      else if existing_user_with_email:
        → 409 with body { error: "email_in_use",
                          hint: "Sign in first then link Google in settings." }
      else:
        → create user(email, full_name, is_verified=True, hashed_password=None)
        → insert oauth_identities row
        → fire user_registered + oauth_signin_completed(was_new=True)  [new signup]
  - mint session JWT (purpose="session")
  - oauth_states.result_token = jwt; row TTL stays at 5min total
  - 302 → /account/oauth-complete?id={state_id}

SPA on /account/oauth-complete:
  - reads ?id from URL
  - POST /api/auth/oauth/exchange { id }
  - server: look up row, return { access_token, redirect_to }, delete row
  - SPA: localStorage["regstack.access_token"] = access_token
  - SPA: window.location = redirect_to
```

### Link flow (authenticated user adds Google to an existing account)

Differences vs sign-in:

- `/oauth/google/link/start` requires `current_user`. The state row
  is inserted with `mode="link"` and `linking_user_id=<user.id>`.
- The callback's resolution step is different:
  - identity already exists for some other user → 409 `identity_in_use`.
  - identity already exists for *this* user → 409 `already_linked`.
  - identity doesn't exist → insert it, attach to `linking_user_id`.
- The result token in this flow is just a confirmation, not a fresh
  session JWT (the user's existing session continues). The SSR
  `/account/oauth-complete` page handles either kind.

### Unlink

`DELETE /api/auth/oauth/google/link` — authenticated.

- Refuse with 400 if it's the user's only sign-in method (no
  password set, no other OAuth identity). Error: `last_auth_method`.
- Otherwise delete the row.
- Fire `oauth_account_unlinked`.

---

## Token handoff

The OAuth callback is a top-level browser navigation. The SPA can't
intercept it. Three options were considered:

1. **Cookie session.** Set `Set-Cookie: regstack_access=… HttpOnly Secure SameSite=Lax`. Rejected because regstack is bearer-only today; introducing cookies means CSRF middleware needs to land in the same change. Out of scope.
2. **Token in URL fragment.** `#access_token=…`. Rejected because tokens leak into browser history.
3. **One-time exchange code.** Random `id` in URL, server-stored token. **Selected.** Reuses the `oauth_states` row (we already have a row keyed by `id`; just populate `result_token` on the way out). 30-second window between callback and exchange; row is deleted after a successful exchange. CSP-clean — no inline script, no token in URL.

---

## Configuration additions

```python
class OAuthConfig(BaseModel):
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: AnyHttpUrl | None = None
    # ↑ defaults to f"{config.base_url}{config.api_prefix}/oauth/google/callback"

    auto_link_verified_emails: bool = False
    # See tasks/oauth-design.md §1 for the threat model.
    # Off by default: requires the user to sign in then explicitly link.
    # On: any Google login matching an existing email auto-links.

    enforce_mfa_on_oauth_signin: bool = False
    # See §"MFA interaction". Off by default: an OAuth sign-in completes
    # without going through SMS MFA on the assumption that the OAuth
    # provider already authenticated the human. Hosts in regulated
    # environments may want this on.

    state_ttl_seconds: int = 300
    completion_ttl_seconds: int = 30
```

`RegStackConfig.oauth: OAuthConfig = OAuthConfig()`. The router is
mounted when `enable_oauth=True AND oauth.google_client_id` is set
(both required — being explicit about which provider, not just
"OAuth on").

---

## Model changes (the one that will surprise you)

`BaseUser.hashed_password: str` → `str | None`.

A user who signed up via Google has no password. The change ripples
through:

- **Login route.** Already returns 401 on bad credentials. New
  branch: `if user.hashed_password is None: return 401` (don't
  reveal "this user uses OAuth-only" to an attacker).
- **Password reset flow.** OAuth-only users *can* go through the
  reset flow — it acts as "set your initial password". The reset
  endpoint already doesn't require an old password.
- **Password change flow.** Currently requires the old password. For
  OAuth-only users, return 400 `no_password_set` and tell them to
  use the reset flow.
- **Unlink-the-last-auth-method check.** Refuses to unlink if
  `hashed_password is None AND no_other_oauth_identity`.
- **SQL migration.** New Alembic revision that makes
  `users.hashed_password` nullable. Existing rows are unaffected
  (they all have values).
- **Pydantic model.** Default `None`. Validators on create paths
  that go through the password-based registration must still
  enforce non-empty.

---

## MFA interaction (default: OAuth bypasses regstack SMS MFA)

The argument: regstack's SMS MFA exists to push back on
credential-stuffing — attackers who have a password but not the
phone. An OAuth sign-in was *not* credential-stuffed; Google
authenticated the user. Adding regstack's SMS step on top is
hostile UX for marginal additional security (Google itself is more
trustworthy than SMS, given SIM-swap attacks).

The argument against: in regulated environments the regstack-side
MFA may be a compliance requirement. Hence
`enforce_mfa_on_oauth_signin: bool = False` — opt-in.

When the flag is on and the user has SMS MFA enabled, the OAuth
callback returns an `mfa_pending` JWT instead of a session JWT (same
shape as `/login`), and the SPA goes through `/login/mfa-confirm`
exactly as for a password sign-in.

---

## Provider abstraction (so GitHub fits later)

```python
class OAuthProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        nonce: str,
    ) -> str:
        """Build the URL to redirect the browser to."""

    @abstractmethod
    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthTokens:
        """Trade auth code for an ID token + access token."""

    @abstractmethod
    async def verify_id_token(
        self,
        id_token: str,
        *,
        expected_nonce: str,
    ) -> OAuthUserInfo:
        """Verify signature + standard claims, return canonical user info."""


@dataclass(frozen=True, slots=True)
class OAuthUserInfo:
    subject_id: str
    email: str | None
    email_verified: bool
    full_name: str | None
    picture_url: str | None


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str
    id_token: str
    refresh_token: str | None
```

Concrete `GoogleProvider`:

- Hard-codes the OIDC endpoints (`accounts.google.com/.well-known/openid-configuration`
  is the spec-correct approach but the URLs are stable and we'd be
  fetching a 4KB JSON doc to learn 3 URLs we already know).
- Uses `pyjwt[crypto]` + `PyJWKClient` against
  `https://www.googleapis.com/oauth2/v3/certs` (cached).
- Validates `iss == "https://accounts.google.com"`, `aud ==
  client_id`, `exp > now`, `nonce == expected`.

A `OAuthRegistry` on the `RegStack` façade maps name → provider:
`rs.oauth.get_provider("google") -> GoogleProvider`. v1 has only
"google".

---

## Library choice: `httpx` + `pyjwt[crypto]`, not `authlib`

Considered: [`authlib`](https://docs.authlib.org/) (fully-featured),
hand-rolled (`httpx` for HTTP, `pyjwt` for JWT verification).

**Picked: hand-rolled.** Reasons:

- Google OIDC is ~150 lines hand-rolled. `pyjwt` already does
  signature + standard-claim validation when handed a key.
  `PyJWKClient` does JWKS fetch + cache.
- `authlib` is a heavyweight dep aimed at also being an OAuth
  provider, JWE, etc. We need ~5% of its surface area.
- Adding `pyjwt[crypto]` (which pulls `cryptography`) is one new
  transitive dep regardless of which library we pick — we need
  RSA for ID-token signature verification either way.

New optional extra:

```toml
[project.optional-dependencies]
oauth = ["pyjwt[crypto]>=2.8"]
```

The `oauth/` modules import `pyjwt`'s crypto bits lazily so the
package keeps importing on a base install (lesson from 0.2.1).

---

## Routes (precise)

```
GET    /api/auth/oauth/google/start
       ?redirect_to=/account/me                 (optional, default /account/me)
       → 302 Location: https://accounts.google.com/...

GET    /api/auth/oauth/google/callback
       ?code=...&state=...
       → 302 Location: /account/oauth-complete?id={state_id}    [happy path]
       → 302 Location: /account/login?error=oauth_failed         [any error]

POST   /api/auth/oauth/exchange
       Body: { id: string }
       Response: { access_token, redirect_to, was_new_account }
       → 200 OK on first call, 404 on second call (single-use)

POST   /api/auth/oauth/google/link/start
       (authenticated)
       → 200 { authorization_url }   — SPA navigates the browser to this URL

DELETE /api/auth/oauth/google/link
       (authenticated)
       → 200 OK / 400 last_auth_method
```

UI routes:

```
GET    /account/oauth-complete                  (SSR)
       — tiny stateless page; regstack.js does the exchange + redirect.
```

The login page (`/account/login`) and the account page
(`/account/me`) gain new sections (button + connected-accounts
list). All template overrides remain available via
`add_template_dir`.

---

## Hooks (new event names)

Adds to `regstack.hooks.events.KNOWN_EVENTS`:

- `oauth_signin_started` — kwargs: `provider: str`, `mode: str`
- `oauth_signin_completed` — kwargs: `user: BaseUser`,
  `provider: str`, `mode: str`, `was_new: bool`
- `oauth_account_linked` — kwargs: `user: BaseUser`,
  `provider: str`
- `oauth_account_unlinked` — kwargs: `user: BaseUser`,
  `provider: str`

A host that wants Mixpanel-style "OAuth signup" events writes one
hook handler — same pattern as the existing `user_registered` hook.

---

## Storage layer

### Protocols (new)

```python
class OAuthIdentityRepoProtocol(Protocol):
    async def find_by_subject(
        self, *, provider: str, subject_id: str
    ) -> OAuthIdentity | None: ...

    async def list_for_user(self, user_id: str) -> list[OAuthIdentity]: ...

    async def create(self, identity: OAuthIdentity) -> OAuthIdentity: ...

    async def delete(self, *, user_id: str, provider: str) -> bool: ...

    async def delete_by_user_id(self, user_id: str) -> int: ...
    # called from delete-account flow

    async def touch_last_used(
        self, *, provider: str, subject_id: str, when: datetime
    ) -> None: ...

class OAuthStateRepoProtocol(Protocol):
    async def create(self, state: OAuthState) -> None: ...
    async def find(self, state_id: str) -> OAuthState | None: ...
    async def set_result_token(self, state_id: str, token: str) -> None: ...
    async def consume(self, state_id: str) -> OAuthState | None: ...
        # atomic: read + delete. Returns None if missing.
    async def purge_expired(self, now: datetime | None = None) -> int: ...
```

### Mongo

- New collections `oauth_identities`, `oauth_states`.
- Indexes:
  - `oauth_identities`: `{provider:1, subject_id:1}` UNIQUE,
    `{user_id:1, provider:1}` UNIQUE.
  - `oauth_states`: `{expires_at:1}` TTL.

### SQL

- New tables in `schema.py`.
- New Alembic migration `0002_oauth.py`:
  - `CREATE TABLE oauth_identities` with the two unique constraints.
  - `CREATE TABLE oauth_states`.
  - `ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL`.

The `users.hashed_password` change is in the same migration so the
upgrade is atomic.

---

## Security: failure modes considered

| Threat | Mitigation |
|---|---|
| CSRF on callback | `state` param is the row ID; row TTL'd. Replay → row missing → 400. |
| Code replay | Google enforces single-use codes; we additionally consume the state row on callback. |
| Token replay (SPA exchange) | `oauth_states` row deleted on first successful exchange. |
| Open redirect via `redirect_to` | At `/start`, validate same-origin. Reject if scheme/host differs from `config.base_url`. |
| Email-recycling account takeover | Default `auto_link_verified_emails=False`. Documented threat model on the flag. |
| ID-token forgery | Verify signature against Google JWKS, validate `iss`, `aud`, `exp`, `nonce`. Reject otherwise. |
| Subject collision across providers | Unique on `(provider, subject_id)`. Two providers with the same `sub` cannot collide because `provider` differs. |
| Mixed-up linking flow | `mode` + `linking_user_id` are stored server-side in the row, not in the URL. Tampering means tampering with the row, which requires DB access. |
| Privilege escalation | OAuth-created users get default privileges. Admin status only via `bootstrap_admin` / admin endpoints. |
| Long-lived OAuth-issued sessions surviving password change | OAuth sessions are normal session JWTs — same `tokens_invalidated_after` bulk-revoke applies. ✓ |
| Account deletion leaks identity rows | `delete-account` calls `oauth_identities.delete_by_user_id()` in the same transaction (SQL) / sequence (Mongo). |

---

## Test plan (parametrized over SQLite + Mongo + Postgres)

Fake Google fixture: a tiny in-test OAuth provider that

- accepts `/oauth/google/start` redirects to a fake auth URL,
- accepts callback with a fake code, returns canned tokens,
- signs a fake ID token with a known RSA key whose JWKS the test
  injects into the `GoogleProvider` (or via DI: pass an explicit
  `GoogleProvider(jwks_url=fake)`).

`pytest-httpx` or `respx` to mock outbound HTTP cleanly.

Cases:

1. **New signup.** Unknown email + unknown subject → user created
   with `is_verified=True`, identity row inserted, session JWT minted.
2. **Repeat sign-in.** Known subject → user found, session minted,
   `last_used_at` updated.
3. **Block auto-link by default.** Existing email-registered user,
   no identity, default flag (false) → 409 `email_in_use`.
4. **Allow auto-link with flag.** Same setup, flag on,
   `email_verified=true` → links and signs in.
5. **Refuse auto-link when `email_verified=false`.** Flag on, but
   the ID token claims `email_verified=false` → 409 anyway.
6. **CSRF protection.** Callback with a stale/missing state → 400.
7. **State expiry.** Insert state, fast-forward past TTL via
   `frozen_clock`, callback → 400.
8. **Linking the wrong user.** Authenticated user A starts link;
   user B somehow obtains the state ID (assume worst case) — the
   row is bound to user A's ID, callback links to A. Tested by
   manipulating cookies/headers between start and callback.
9. **Identity already linked to someone else.** New user trying to
   link a Google identity that's on an existing user → 409
   `identity_in_use`.
10. **Idempotent re-link.** Already-linked user tries to link the
    same Google account → 409 `already_linked` (or arguably 200 OK
    no-op; design choice). I'd return 409 to be loud.
11. **Unlink.** Linked user with a password unlinks → row deleted,
    fires `oauth_account_unlinked`.
12. **Refuse to unlink last auth method.** OAuth-only user
    (no password, only one identity) attempts unlink → 400
    `last_auth_method`.
13. **OAuth-only user uses password reset to set initial password.**
    Reset flow accepts a user with `hashed_password=None`, sets it,
    bumps `tokens_invalidated_after`.
14. **Account deletion cascades.** OAuth-linked user deletes
    account; identity rows are gone.
15. **Token-handoff round-trip.** Callback sets `result_token`;
    `/oauth/exchange` returns it; second `/oauth/exchange` call
    with the same id returns 404.
16. **Bulk revoke applies to OAuth-issued sessions.** OAuth signin
    → password change → old session 401 (already covered for
    password sessions; one new test asserts the same for
    OAuth-issued JWTs).

All sixteen run against every backend. Fake-Google mocks live in
`tests/_fake_google/` so they're shared.

---

## Files added / modified

```
src/regstack/oauth/
  __init__.py
  base.py                   # OAuthProvider, OAuthTokens, OAuthUserInfo
  errors.py                 # OAuthError, EmailInUseError, IdentityInUseError, …
  registry.py               # OAuthRegistry
  providers/
    __init__.py
    google.py               # GoogleProvider — ~150 lines

src/regstack/models/
  oauth_identity.py         # OAuthIdentity model
  oauth_state.py            # OAuthState model
  user.py                   # hashed_password: str -> str | None

src/regstack/backends/
  protocols.py              # + OAuthIdentityRepoProtocol, OAuthStateRepoProtocol
  base.py                   # Backend gains .oauth_identities, .oauth_states
  factory.py                # no change — kind detection unchanged
  mongo/
    __init__.py             # export new repos
    indexes.py              # + oauth indexes
    repositories/
      oauth_identity_repo.py
      oauth_state_repo.py
  sql/
    schema.py               # + oauth_identities_table, oauth_states_table; users.hashed_password nullable
    repositories/
      oauth_identity_repo.py
      oauth_state_repo.py
    migrations/versions/
      0002_oauth.py

src/regstack/routers/
  __init__.py               # mount oauth router when enable_oauth + google_client_id
  oauth.py                  # build_oauth_router

src/regstack/config/schema.py    # OAuthConfig sub-model

src/regstack/app.py         # wire OAuthRegistry, repos onto RegStack façade

src/regstack/ui/
  templates/
    auth/login.html         # add "Sign in with Google" button (when OAuth enabled)
    auth/me.html            # add "Connected accounts" section
    auth/oauth-complete.html  # new SSR page for the exchange round-trip
  static/js/regstack.js     # add the oauth-complete page handler

tests/integration/
  test_oauth_google.py      # the 16 cases above
tests/_fake_google/
  __init__.py
  provider.py               # in-test OAuth provider impl + JWKS

docs/
  oauth.md                  # new guide page
  index.md                  # add OAuth bullet to "What's in the box"
  changelog.md              # 0.3.0 entry
  api.md                    # add the oauth section

pyproject.toml              # + [project.optional-dependencies].oauth
```

Total new code estimate: ~1500 lines across providers + repos +
routes + templates. ~600 lines of tests.

---

## Build sequence (milestones — one PR each)

1. **M1 — Provider abstraction + GoogleProvider, no routes yet.**
   `OAuthProvider` ABC, `OAuthUserInfo`, `OAuthTokens`,
   `GoogleProvider` with unit tests against a fake JWKS. No router
   mount. No model changes. PR is self-contained.

2. **M2 — Storage layer.** `OAuthIdentityRepoProtocol`,
   `OAuthStateRepoProtocol`, Mongo + SQL implementations, schema
   migration `0002_oauth.py`, `users.hashed_password` made
   nullable. Unit tests for the repos + the migration's autogen-drift
   check.

3. **M3 — Routes + token handoff + JSON-only.** `build_oauth_router`,
   the four endpoints, `oauth_complete.html` page, `regstack.js`
   exchange handler. `oauth_signin_*` hook events. The 16 integration
   tests. *Ships closed-beta-ready functionality.*

4. **M4 — UI polish.** "Sign in with Google" button on login,
   "Connected accounts" section on `/account/me`, error display.
   Screenshots updated. Docs page (`docs/oauth.md`) written.

5. **M5 — Release cut: 0.3.0.** CHANGELOG entry calling out the
   `users.hashed_password` schema change and the
   `auto_link_verified_emails` security flag. Sphinx build clean.

Stops here. GitHub / Microsoft / Apple are post-0.3.0, one minor
each.

---

## Open questions for the user

These need a decision before M1 lands. None are blockers — defaults
are listed — but you may want to override:

1. **Default for `auto_link_verified_emails`.** I'm defaulting to
   `False` (security-first). If your hosts will hate that UX,
   `True` would match consumer-app convention. **Default: False.**
2. **Default for `enforce_mfa_on_oauth_signin`.** I'm defaulting to
   `False` (Google authenticated the user). Compliance-heavy hosts
   may want `True`. **Default: False.**
3. **Should `users.hashed_password` becoming nullable land in 0.3.0
   even before Google OAuth itself?** It's a safer rollout to
   migrate the schema in a 0.2.x point release, then add OAuth in
   0.3.0 — but it doubles the release count. **Default: bundle in
   0.3.0.**
4. **Idempotent re-link: 200 or 409?** Re-linking a Google identity
   already linked to the current user — silent success or loud
   conflict. **Default: 409 `already_linked`** (loud is better for
   debugging; the SPA can hide the error gracefully).
5. **JSON or SSR for `/oauth/google/link/start`?** The link flow
   from `/account/me` could either be a JSON endpoint that returns
   the auth URL (SPA navigates the browser) or a redirect endpoint
   like the public start (server-side `302`). **Default: JSON**
   for symmetry with the rest of the auth API; SPA does the
   navigation.

Reply "default" to take all five defaults; reply with overrides for
any specific one.

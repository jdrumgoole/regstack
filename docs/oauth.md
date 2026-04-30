# OAuth (Sign in with Google)

regstack ships an opt-in OAuth subsystem. v1 supports Google; the
abstraction is shaped so adding GitHub / Microsoft / Apple later is a
new module under `regstack/oauth/providers/` plus one config field.

This page walks a host through enabling it. The full design — including
the threat model and the four-milestone build sequence the
implementation followed — is in
[`tasks/oauth-design.md`](https://github.com/jdrumgoole/regstack/blob/main/tasks/oauth-design.md).

## What you get

When OAuth is enabled and at least one provider is configured:

- **Five JSON endpoints** under `/api/auth/oauth/`:
  - `GET  /oauth/{provider}/start` — public; redirects to the provider.
  - `GET  /oauth/{provider}/callback` — public; handles the redirect back.
  - `POST /oauth/exchange` — single-use; SPA trades the state-id for a
    session JWT.
  - `POST /oauth/{provider}/link/start` — authenticated; returns the URL
    to navigate the browser to.
  - `DELETE /oauth/{provider}/link` — authenticated; unlinks one identity.
  - `GET  /oauth/providers` — authenticated; lists configured + linked
    providers (drives the SSR connected-accounts panel).
- **A "Sign in with Google" button** on the bundled SSR login page.
- **A "Connected accounts" panel** on the SSR `/account/me` page.
- **Four hook events**: `oauth_signin_started`, `oauth_signin_completed`,
  `oauth_account_linked`, `oauth_account_unlinked`.

## Install the extra

```bash
uv add 'regstack[oauth]'
```

The `oauth` extra pulls in `pyjwt[crypto]>=2.8`, which transitively
includes `cryptography`. ID-token signature verification needs RSA, so
this is unavoidable.

## Register a Google client

In the [Google Cloud Console](https://console.cloud.google.com/apis/credentials):

1. Create an **OAuth 2.0 Client ID** of type **Web application**.
2. Add an **Authorized redirect URI** that exactly matches the URL
   regstack will receive callbacks at — by default that's
   `<your base_url><api_prefix>/oauth/google/callback`. For a local
   dev server with the defaults that's
   `http://localhost:8000/api/auth/oauth/google/callback`.
3. Copy the **client ID** and **client secret** out — you'll set them
   on regstack next.

## Configure regstack

```toml
# regstack.toml
enable_oauth = true

[oauth]
google_client_id = "12345.apps.googleusercontent.com"
# google_client_secret lives in regstack.secrets.env
# google_redirect_uri = "https://your.app/api/auth/oauth/google/callback"   # optional override
auto_link_verified_emails = false   # security choice — see below
```

```bash
# regstack.secrets.env
REGSTACK_OAUTH__GOOGLE_CLIENT_SECRET=...
```

The router is mounted only when `enable_oauth=true` AND
`google_client_id` AND `google_client_secret` are all set.

## The account-linking decision

When a Google sign-in arrives carrying an email that already belongs to
a regstack user (created via password registration), regstack has to
choose between three policies:

| Policy | Behaviour |
|---|---|
| **Refuse** (default) | Return `?error=email_in_use` on the redirect. The user must sign in with their existing password, then link Google from `/account/me`. |
| **Auto-link verified** | If Google's `email_verified=true`, silently link the new identity to the existing user. UX win, but trusts Google's email-verified claim *forever*. |
| **Always create new** | Make a second account. |

regstack defaults to **refuse**. To opt into auto-linking — accepting
that an attacker who later acquires a recycled Gmail address could sign
in as the original regstack user — set
`oauth.auto_link_verified_emails = true`.

The full threat-model writeup is in
[`tasks/oauth-design.md`](https://github.com/jdrumgoole/regstack/blob/main/tasks/oauth-design.md).

## OAuth-only users

A Google sign-up creates a regstack user with `hashed_password = None`.
Three knock-on effects, all handled:

- **Login route** rejects password attempts on these accounts with the
  same generic 401 a wrong-password attempt gets — never reveal that
  an account exists but has no password.
- **`change-password` / `change-email` / `delete-account`** all need
  the current password. For OAuth-only users they return 400 with a
  pointer at the password-reset flow, which doubles as a "set initial
  password" path.
- **`DELETE /oauth/{provider}/link`** refuses if it would remove the
  user's only sign-in method (no password set, no other linked
  provider). The error is `400 last sign-in method`.

## Hooks

```python
@regstack.on("oauth_signin_completed")
async def _track_signin(*, user, provider, mode, was_new):
    if was_new:
        await analytics.track("signup", {"user": user.id, "provider": provider})
    else:
        await analytics.track("login", {"user": user.id, "provider": provider})


@regstack.on("oauth_account_linked")
async def _notify_link(*, user, provider):
    await mailer.send_link_notification(to=user.email, provider=provider)
```

The full event list is in the
[architecture guide](architecture.md#hooks).

## Disabling OAuth

Flip `enable_oauth = false` (or leave the credentials unset). The
router won't mount; the SSR login page won't render the button; the
`/me` panel hides the section. No other configuration changes are
required.

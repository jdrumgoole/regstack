# Embedding regstack in a host app

[Quickstart](quickstart.md) covers the minimum-viable embed —
construct a `RegStack`, mount its router, install the schema. This
page covers the patterns most hosts adopt next: choosing a backend,
hooking into events, plugging in a different email provider,
overriding templates and themes, and running multiple regstacks in one
process.

## Picking a backend

The default `database_url` is SQLite, so a host that does nothing gets
a working backend with no infrastructure. To switch:

```toml
# regstack.toml

# SQLite (default — file lives wherever the path points)
database_url = "sqlite+aiosqlite:///./dbname.db"

# Postgres (needs the `postgres` extra → asyncpg)
database_url = "postgresql+asyncpg://<username>:<password>@dbhost.example.com:5432/dbname"

# MongoDB (needs the `mongo` extra → pymongo)
database_url = "mongodb://<username>:<password>@dbhost.example.com:27017/dbname"
```

Hosts that already manage their own connection pool — for example, an
app that talks to Postgres for its own data and wants regstack to
reuse the same engine — can skip the URL and pass an explicit Backend:

```python
from regstack.backends.sql import SqlBackend
from regstack.backends.base import BackendKind

backend = SqlBackend(config=config, clock=SystemClock(), kind=BackendKind.POSTGRES)
regstack = RegStack(config=config, backend=backend)
```

## Subscribing to events

regstack fires events at the natural points in the auth lifecycle, and
the host subscribes via `regstack.on(event, handler)`. This is how you
push a newly-registered user into your CRM, kick off welcome
automation, or clean up host data when a user deletes their account —
without modifying regstack:

```python
@regstack.on("user_registered")
async def _send_to_crm(user) -> None:
    await crm.upsert_contact(email=user.email, full_name=user.full_name)


@regstack.on("user_deleted")
async def _purge_host_data(user) -> None:
    await my_app.delete_all_data_for(user.id)


@regstack.on("oauth_signin_completed")
async def _track_signin(*, user, provider, mode, was_new) -> None:
    if was_new:
        await analytics.track("signup", {"user": user.id, "provider": provider})
    else:
        await analytics.track("login", {"user": user.id, "provider": provider})
```

Handlers can be sync or async. Exceptions are logged but never break
the primary auth flow — see [`HookRegistry`](architecture.md#hooks).
The full event list (including the four OAuth events:
``oauth_signin_started``, ``oauth_signin_completed``,
``oauth_account_linked``, ``oauth_account_unlinked``) is in the
architecture guide.

## Custom email or SMS backends

The bundled backends cover `console` (dev), SMTP,
[Amazon SES](https://aws.amazon.com/ses/),
[Amazon SNS](https://aws.amazon.com/sns/), and
[Twilio](https://www.twilio.com/). To plug in something else
(Postmark, SendGrid, MessageBird, …) implement the `EmailService` or
`SmsService` ABC — one async method:

```python
from regstack.email.base import EmailMessage, EmailService


class PostmarkEmailService(EmailService):
    def __init__(self, server_token: str) -> None:
        self._token = server_token

    async def send(self, message: EmailMessage) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://api.postmarkapp.com/email",
                headers={
                    "X-Postmark-Server-Token": self._token,
                    "Accept": "application/json",
                },
                json={
                    "From": message.from_header,
                    "To": message.to,
                    "Subject": message.subject,
                    "HtmlBody": message.html,
                    "TextBody": message.text,
                },
            )


regstack.set_email_backend(PostmarkEmailService(server_token=os.environ["POSTMARK"]))
```

The same pattern applies for SMS via `SmsService` and
`set_sms_backend(...)`.

## Enabling OAuth

Install the extra and configure a provider:

```bash
uv add 'regstack[oauth]'
```

```toml
# regstack.toml
enable_oauth = true

[oauth]
google_client_id = "12345.apps.googleusercontent.com"
# google_client_secret in regstack.secrets.env
auto_link_verified_emails = false  # security default — see oauth.md
```

The router mounts five JSON endpoints under `/oauth/` (start /
callback / exchange / link-start / unlink) plus a `/oauth/providers`
list. The bundled SSR pages pick up the rest automatically: a
"Sign in with Google" button on `/account/login` and a Connected-
accounts panel on `/account/me`.

Hosts that need a custom provider (Apple, Microsoft, an internal
OIDC) can register one programmatically on the registry:

```python
rs.oauth.register(MyCustomProvider(...))
```

Anything implementing :class:`~regstack.oauth.base.OAuthProvider`
works — three abstract methods (`authorization_url`,
`exchange_code`, `verify_id_token`). The router parametrizes its
URL paths on the provider name, so a registered provider named
``"github"`` is reachable at `/oauth/github/start` without any
router changes.

The full host guide — Google client setup, the linking-policy
decision, OAuth-only-user knock-on effects — is in
[OAuth](oauth.md).

## Overriding email and HTML templates

Both surfaces share `RegStack.add_template_dir(path)`:

```python
regstack.add_template_dir(Path("/app/host/templates"))
```

Drop a same-named file into your directory to win against the bundled
default — regstack uses Jinja2's `ChoiceLoader` so the host directory
is searched first. Examples:

- `auth/login.html` — replaces the SSR sign-in page.
- `verification.html` / `verification.txt` /
  `verification.subject.txt` — replaces the verification email.
- `sms_login_mfa.txt` — replaces the body of the MFA login SMS.

A list of every overridable file lives in
`src/regstack/email/templates/` and `src/regstack/ui/templates/`.

## Switching the SSR theme without templates

If you only want to flip colors / fonts, you don't need to override
any templates — just supply a CSS file that overrides the bundled
CSS custom properties:

```toml
# regstack.toml
theme_css_url = "/static/my-theme.css"
```

regstack loads `core.css` → bundled `theme.css` → host
`theme_css_url`, so a host file overriding only the `--rs-*` variables
flips every page. See [Theming](theming.md).

## Multiple regstacks in one process

Two regstacks in the same FastAPI app — for example a B2C tenant
under `/api/auth` and a B2B tenant under `/admin/auth`:

```python
b2c = RegStack(config=b2c_cfg)
b2b = RegStack(config=b2b_cfg)

app.include_router(b2c.router, prefix="/api/auth")
app.include_router(b2b.router, prefix="/admin/auth")
```

Each instance owns its own dependencies, so authenticating against
one does not validate against the other. The
`current_user`/`current_admin` deps come from `regstack.deps.current_user()`
(a closure factory) so they cannot leak between instances.

## Bootstrapping the first admin

```bash
uv run regstack create-admin --email admin@app.example.com
```

The CLI prompts for a password (with confirmation). Re-running with
an existing email promotes the existing user to admin without changing
their password.

In code:

```python
await regstack.bootstrap_admin("admin@app.example.com", "long-strong-password")
```

This is idempotent — promotes an existing user, creates one if not
present.

## Health-check and probes

```bash
uv run regstack doctor [--config ...] [--check-dns] [--send-test-email <addr>]
```

`doctor` reports JWT secret strength, database reachability, indexes,
the email backend's instantiability, and optionally DNS (SPF/DKIM/MX)
and a real email send. Exit code is the number of failed checks —
wire it into a container health check or a Kubernetes liveness probe
for production probes that need more than a TCP hit.

## What regstack does *not* do

- It does not mount a CSRF middleware. The bundled SSR pages don't
  use cookies, so they don't need it; if you swap the bundled JS for
  a cookie-based variant, configure CSRF at the host.
- It does not enforce HTTPS. Run behind a TLS terminator.
- It does not provision SES identities, Route 53 records, IAM users,
  or anything else outside the database.
- It does not ship OAuth providers other than Google. The
  abstraction is shaped to take GitHub / Microsoft / Apple later;
  hosts that want a different provider today can implement
  :class:`~regstack.oauth.base.OAuthProvider` and register it.

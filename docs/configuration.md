# Configuration

regstack reads its configuration from one `RegStackConfig` (a
`pydantic_settings.BaseSettings`). The same fields are addressable from
TOML files, environment variables, and programmatic kwargs.

## Loading order

Highest priority wins:

1. Programmatic kwargs to `RegStackConfig(...)` or `RegStackConfig.load(...)`.
2. Real environment variables (`REGSTACK_*`).
3. The `regstack.secrets.env` file in the current directory (or whatever
   path you pass as `secrets_env_path=`). Lines look like
   `REGSTACK_JWT_SECRET=...`.
4. A TOML file at `$REGSTACK_CONFIG`, or `./regstack.toml` if present, or
   whatever path you pass as `toml_path=`.
5. Field defaults.

Nested settings (`email.from_address`, `sms.twilio_account_sid`) are
addressed in env using a `__` separator: `REGSTACK_EMAIL__FROM_ADDRESS`.

## Top-level fields

```{list-table}
:header-rows: 1
:widths: 25 20 55

* - Field
  - Default
  - Notes

* - `app_name`
  - `"RegStack"`
  - Branded into UI templates and email subjects.
* - `base_url`
  - `http://localhost:8000`
  - Origin used to build verification / reset / email-change links.
* - `cookie_domain`
  - `None`
  - Reserved for cookie-transport mode.
* - `behind_proxy`
  - `false`
  - Tells the host app to trust `X-Forwarded-*`.
* - `database_url`
  - `sqlite+aiosqlite:///./regstack.db`
  - SecretStr. Backend selected by URL scheme — see "Backends" below.
* - `mongodb_database`
  - `"regstack"`
  - Mongo-only fallback when the URL has no `/dbname` path.
* - `user_collection`
  - `"users"`
  - Table / collection name. Override to share a database with another app.
* - `pending_collection`
  - `"pending_registrations"`
  -
* - `blacklist_collection`
  - `"token_blacklist"`
  -
* - `login_attempt_collection`
  - `"login_attempts"`
  -
* - `mfa_code_collection`
  - `"mfa_codes"`
  -
```

## Backends

regstack picks a backend at construction time from the URL scheme of
``database_url``:

```{list-table}
:header-rows: 1
:widths: 18 38 44

* - Backend
  - URL scheme
  - Notes

* - SQLite
  - `sqlite+aiosqlite:///./path.db`
  - Default. Bundled in the base install — no extras needed.
    `:memory:` works too (per-test).
* - Postgres
  - `postgresql+asyncpg://user:pw@host/dbname`
  - Requires the `postgres` extra (pulls in `asyncpg`). The driver is
    pinned to `+asyncpg` — sync drivers won't work.
* - MongoDB
  - `mongodb://host:port/dbname` (or `mongodb+srv://`)
  - Requires the `mongo` extra (pulls in `pymongo`). Database is taken
    from the URL path; falls back to ``mongodb_database`` if absent.
```

The active backend exposes the same five repository protocols on
``RegStack.users``, ``.pending``, ``.blacklist``, ``.attempts``,
``.mfa_codes``. Routers / hooks never branch on backend kind.

## JWT

```{list-table}
:header-rows: 1
:widths: 30 15 55

* - Field
  - Default
  - Notes

* - `jwt_secret`
  - (generated)
  - 64-byte URL-safe by default. `RegStack.__init__` raises if empty.
* - `jwt_algorithm`
  - `"HS256"`
  - One of `HS256`, `HS384`, `HS512`.
* - `jwt_ttl_seconds`
  - `7200`
  - Session token lifetime.
* - `jwt_audience`
  - `None`
  - Sets the `aud` claim if non-null and validates on decode.
* - `transport`
  - `"bearer"`
  - `"cookie"` is reserved for a future milestone.
* - `verification_token_ttl_seconds`
  - `86400`
  - 24h.
* - `password_reset_token_ttl_seconds`
  - `1800`
  - 30 min.
* - `email_change_token_ttl_seconds`
  - `3600`
  - 1 h.
```

## Feature flags

```{list-table}
:header-rows: 1
:widths: 30 15 55

* - Flag
  - Default
  - Effect

* - `require_verification`
  - `true`
  - Register stores in `pending_registrations` until the verification email is clicked.
* - `allow_registration`
  - `true`
  - When false, `/register` returns 403.
* - `enable_password_reset`
  - `true`
  - Mounts `/forgot-password` and `/reset-password`.
* - `enable_account_deletion`
  - `true`
  - When false, `DELETE /account` returns 404.
* - `enable_admin_router`
  - `false`
  - Mounts `/admin/*` routes (requires `is_superuser`).
* - `enable_ui_router`
  - `false`
  - Mounts the SSR pages.
* - `enable_sms_2fa`
  - `false`
  - Mounts `/phone/*` routes and gates the MFA second step in `/login`.
* - `enable_oauth`
  - `false`
  - Reserved. No providers ship in v1.
```

## Lockout (login)

```{list-table}
:header-rows: 1
:widths: 35 15 50

* - Field
  - Default
  - Notes

* - `rate_limit_disabled`
  - `false`
  - Set in tests to skip lockout writes.
* - `login_lockout_threshold`
  - `5`
  - Fail this many times in the window → 429.
* - `login_lockout_window_seconds`
  - `900`
  - Sliding window for failures, also TTL on the `login_attempts` collection.
* - `login_max_per_minute`
  - `5`
  - Reserved for a future route-level rate limiter.
* - `login_max_per_hour`
  - `20`
  - Same reservation.
```

## SMS / 2FA

```{list-table}
:header-rows: 1
:widths: 35 15 50

* - Field
  - Default
  - Notes

* - `sms_code_length`
  - `6`
  - Numeric code length.
* - `sms_code_ttl_seconds`
  - `300`
  - 5 minutes.
* - `sms_code_max_attempts`
  - `5`
  - Wrong-code attempts before the row is deleted (lockout).
* - `mfa_pending_token_ttl_seconds`
  - `600`
  - Lifetime of the JWT that links the two login steps / the phone-setup steps.
```

## Sub-tables

`[email]` (`EmailConfig`):

```toml
[email]
backend = "console"             # console | smtp | ses
from_address = "noreply@…"
from_name    = "MyApp"

# smtp
smtp_host = "smtp.example.com"
smtp_port = 587
smtp_starttls = true
smtp_username = "myapp"
# smtp_password is a SecretStr — set via REGSTACK_EMAIL__SMTP_PASSWORD

# ses
ses_region = "eu-west-1"
ses_profile = "production"
```

`[sms]` (`SmsConfig`):

```toml
[sms]
backend = "null"                # null | sns | twilio
from_number = "+15555550100"

# sns
sns_region = "eu-west-1"

# twilio
twilio_account_sid = "AC…"
# twilio_auth_token via REGSTACK_SMS__TWILIO_AUTH_TOKEN
```

## SSR / theming

```{list-table}
:header-rows: 1
:widths: 30 30 40

* - Field
  - Default
  - Notes

* - `api_prefix`
  - `"/api/auth"`
  - Used by `regstack.js` to call back into the JSON API.
* - `ui_prefix`
  - `"/account"`
  - Used for self-referential links on SSR pages.
* - `static_prefix`
  - `"/regstack-static"`
  - Where `app.mount(... regstack.static_files)` should live.
* - `theme_css_url`
  - `None`
  - Optional URL of a host-supplied `theme.css` loaded after the bundled defaults.
* - `brand_logo_url`
  - `None`
  - Rendered in the SSR header.
* - `brand_tagline`
  - `None`
  - Subtitle in the SSR header.
* - `extra_template_dirs`
  - `[]`
  - Directories prepended to the Jinja2 ChoiceLoader (host-first).
* - `extra_static_dirs`
  - `[]`
  - Reserved.
```

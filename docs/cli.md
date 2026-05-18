# CLI reference

`regstack` is the entry point installed by the package. All sub-commands
share a config-loading model: programmatic kwargs > env vars >
`regstack.secrets.env` > `regstack.toml` > defaults. The `--config <path>`
flag overrides where the TOML file is found.

## `regstack init`

Interactive wizard that writes `regstack.toml` and `regstack.secrets.env`
in the current directory. Asks which backend to use (SQLite default →
Postgres → MongoDB), generates a 64-byte JWT secret, runs DNS sanity
checks if asked, never provisions infrastructure.

```bash
uv run regstack init
uv run regstack init --target /etc/app --force
```

Options:

- `--target DIR` — directory to write the config files (default cwd).
- `--force` — overwrite without confirming.

Re-running the wizard prompts before overwriting unless `--force` is
passed; pre-existing answers aren't kept (the wizard is intentionally
stateless).

## `regstack oauth setup`

Opens a guided 12-step wizard in a native webview window that walks you
through registering a Google OAuth 2.0 client (project selection,
consent screen, redirect URI, credentials) and merges the result into
your existing `regstack.toml` and `regstack.secrets.env`. The merge is
**non-clobbering** — comments, unrelated tables (`[email]`, `[sms]`,
…), and unrelated top-level keys are preserved. Re-run any time to
rotate credentials or change the linking policy.

```bash
uv run regstack oauth setup
uv run regstack oauth setup --target /etc/app
```

Options:

- `--target DIR` — directory containing (or to receive) `regstack.toml`
  (default cwd).
- `--api-prefix PREFIX` — router prefix the host mounts regstack under
  (default `/api/auth`). Used to compute the suggested redirect URI.
- `--port N` — pin the wizard server's TCP port (default: random free
  port on `127.0.0.1`).
- `--print-only` — skip the GUI; print the TOML + secrets diff that
  *would* be written to stdout, then exit. Useful for headless hosts
  (CI, servers without a webview backend) and dry-run smoke tests.
  Pair with `--client-id`, `--client-secret`, `--base-url`,
  `--auto-link/--no-auto-link`, `--mfa/--no-mfa`.

The interactive mode requires a desktop environment with a webview
backend (WebKit on macOS, GTK / QtWebEngine on Linux, Edge WebView2 on
Windows). On a headless host it exits with a clear error pointing at
`--print-only`.

The wizard binds to `127.0.0.1` only and authenticates every API call
with a per-launch random token, so a hostile process on the same host
can't drive the write endpoint.

## `regstack theme design`

Opens a live designer for `regstack-theme.css` in a native pywebview
window. The left pane has controls for every `--rs-*` CSS custom
property; the right pane renders the bundled SSR widgets (sign-in
form, success / error messages, danger-zone button) with your changes
applied in real time. Click **Save** to write the file; **Reset to
defaults** to start over; **Copy CSS** to put the generated
stylesheet on the clipboard without writing.

```bash
uv run regstack theme design
uv run regstack theme design --target /var/www/static
```

Options:

- `--target DIR` — directory to write `regstack-theme.css` into
  (default cwd).
- `--filename NAME` — output filename
  (default `regstack-theme.css`).
- `--port N` — pin the designer's TCP port (default: random free
  port on `127.0.0.1`).
- `--print-only` — skip the GUI; write the file from `--var` pairs
  and emit a JSON summary. For headless / CI use.
- `--var NAME=VALUE` — repeatable. Used with `--print-only`. Prefix
  with `dark:` to set the dark-scheme value, e.g.
  `--var dark:--rs-accent=#2dd4bf`.

Re-running the designer reloads the previous values out of the file,
so iterating on a theme is non-destructive. Only the `:root` and
`@media (prefers-color-scheme: dark)` blocks are managed by the
designer — anything else in the file is left alone.

Same security shape as `regstack oauth setup`: binds `127.0.0.1`
only, every API call requires a per-launch random token.

## `regstack ses setup`

Opens a guided 9-step wizard in a native webview window that
configures the SES email backend. Validates against AWS as you
go: confirms the chosen credential source authenticates (STS
`GetCallerIdentity`), confirms the sender domain is verified in
SES (`GetIdentityVerificationAttributes`), detects sandbox state
(`GetAccount` with a `GetSendQuota` heuristic fallback for
IAM-restricted policies), and optionally fires one live
`SendEmail` as a probe before persisting. Non-clobbering tomlkit
merge into `regstack.toml` + `regstack.secrets.env`.

```bash
uv run regstack ses setup
uv run regstack ses setup --target /etc/app
```

New in 0.8.0. Gated behind the joint extra:
`pip install 'regstack[wizard,ses]'`. If either extra is missing,
the lazy click group prints a combined install hint and exits
non-zero — running with only one of the two never partially
starts.

Options:

- `--target DIR` — directory containing (or to receive)
  `regstack.toml` (default cwd).
- `--port N` — pin the wizard server's TCP port (default: random
  free port on `127.0.0.1`).
- `--print-only` — skip the GUI; run the same merge logic
  headlessly from the CLI flags and emit a JSON summary. AWS
  state checks (credential resolution, identity verification,
  sandbox, test send) are skipped in this mode — the operator
  is trusted to know the config is correct. Pair with:
  - `--region REGION` — AWS region (e.g. `eu-west-1`). Validated
    against the known SES region list.
  - `--from-address EMAIL` — sender address that lands in
    `[email].from_address`.
  - `--credential-source {profile,explicit,chain}` — which
    credential source to write. `profile` writes
    `[email].ses_profile`; `explicit` writes
    `[email].ses_access_key_id` plus the secret to
    `regstack.secrets.env`; `chain` writes neither (boto3's
    default credential resolution runs at runtime).
  - `--profile NAME` — required when
    `--credential-source=profile`.
  - `--access-key-id KEY` — required when
    `--credential-source=explicit`.
  - `--secret-access-key SECRET` — required when
    `--credential-source=explicit`. Lands in
    `regstack.secrets.env`, never in the TOML.

### Sandbox handling

When the wizard detects the AWS account is in the SES sandbox
(via `ses:GetAccount`, with a 200-messages/day quota heuristic
fallback), it surfaces a self-attested checkbox naming the
limitation ("SES will reject email to any address that isn't
separately verified") and refuses to advance until the operator
ticks it. Graduation out of the sandbox is a manual AWS process
the wizard can't drive; it just makes the trade-off explicit.

If `ses:GetAccount` returns `AccessDenied` (some
least-privilege IAM policies block it), the wizard surfaces a
non-blocking advisory and proceeds, recommending a follow-up
verification before the host ships to production.

### Typical usage

Local dev with a named AWS profile:

```bash
uv run regstack ses setup
# in the window: region eu-west-1, profile mode, profile name "dev"
```

CI / scripted, headless:

```bash
uv run regstack ses setup --print-only \
    --region eu-west-1 \
    --from-address noreply@app.example.com \
    --credential-source chain
```

Containerised prod with explicit credentials (the secret lands
in `regstack.secrets.env`):

```bash
uv run regstack ses setup --print-only \
    --region eu-west-1 \
    --from-address noreply@app.example.com \
    --credential-source explicit \
    --access-key-id "$AWS_ACCESS_KEY_ID" \
    --secret-access-key "$AWS_SECRET_ACCESS_KEY"
```

Verify end-to-end after the wizard writes:

```bash
uv run regstack doctor --send-test-email you@example.com
```

Same security shape as the other two wizards: binds
`127.0.0.1` only, per-launch random token on every API call.

## `regstack create-admin`

Create or promote a superuser. Idempotent.

```bash
uv run regstack create-admin --email admin@app.example.com
uv run regstack create-admin --email admin@app.example.com --password 'long-strong-password'
uv run regstack create-admin --email admin@app.example.com --config /etc/app/regstack.toml
```

Options:

- `--email EMAIL` *(required)*.
- `--password PW` — if omitted, prompts (with confirmation).
- `--config PATH` — TOML file to load (default: env or cwd).

If the user already exists, the command sets `is_superuser=True` and
keeps the existing password. If they don't exist, it creates the user
with `is_active=True`, `is_verified=True`, `is_superuser=True` and the
provided password.

## `regstack migrate`

Runs the bundled Alembic migrations against the configured
`database_url`. Idempotent — re-running on a DB already at the target
revision is a no-op. Use this on SQL backends (SQLite / PostgreSQL)
to roll the schema forward to a new regstack release.

```bash
uv run regstack migrate
uv run regstack migrate --target head
uv run regstack migrate --config /etc/app/regstack.toml --target 0001
```

Options:

- `--config PATH` — TOML file to load (default: cwd / `$REGSTACK_CONFIG`).
- `--target REV` — revision to upgrade to (default `head`). Accepts
  any Alembic revision spec: a revision id (`0001`), a relative step
  (`+1`), or `head`.

Mongo backends are silently skipped: TTL indexes are installed by
`RegStack.install_schema()` on every app start, so there's no separate
migration story to run. Output prints the before / after revision and
exits non-zero if Alembic raises.

## `regstack doctor`

Runs read-only validation against the loaded config and reports each
check on a green/red line. Exit code is the number of failed checks —
suitable for use in container health checks.

```bash
uv run regstack doctor
uv run regstack doctor --config /etc/app/regstack.toml
uv run regstack doctor --check-dns
uv run regstack doctor --send-test-email alice@app.example.com
```

Options:

- `--config PATH` — TOML file to load.
- `--check-dns` — run SPF / DMARC / MX `dig`s on the sender domain.
  Internet-dependent; off by default.
- `--send-test-email TO` — actually send a probe email through the
  configured backend. Costs real money on SES; off by default.

Default checks:

| Check | Pass criterion |
|-------|----------------|
| `jwt secret` | Present, ≥ 32 chars |
| `backend` | `Backend.ping()` succeeds (works for any backend) |
| `schema` | Mongo: required indexes present. SQL: `users` table responds to `count(*)` |
| `email backend` | `build_email_service(config.email)` instantiates |

Optional checks:

| Check | Pass criterion |
|-------|----------------|
| `dns mx` | At least one MX record on the sender domain |
| `dns spf` | A TXT record containing `v=spf1` |
| `dns dmarc` | A TXT record at `_dmarc.<domain>` containing `v=DMARC1` |
| `email send` | The configured backend's `send()` returned without raising |

## `regstack validate`

End-to-end probe of a **deployed** regstack installation. Companion to
`regstack doctor`: doctor checks the loaded config, validate hits the
live JSON API. Registers a throwaway user, walks every auth flow
(verify → login → /me → logout + blacklist → PATCH /me →
change-password → change-email → password reset → OAuth start →
SMS 2FA), then deletes the user. Exit code is the number of failed
checks.

```bash
uv run regstack validate \
    --url https://staging.app.example.com/api/auth \
    --log-source ssh:deploy@staging.app.example.com:/var/log/regstack.log
```

### Preparation

Validate scrapes one-time tokens out of the deployment's stdout, so
the deployment needs three things in place before running it:

1. `email.backend = "console"` in `regstack.toml`. Real SMTP/SES
   backends are rejected because the validator cannot read the
   token out of a real email.
2. `email.log_bodies = true` so the console backend promotes the
   message body (containing the verification / reset / change-email
   URL) from DEBUG to INFO.
3. If you intend to probe SMS 2FA: `sms.backend = "null"` (the
   `null` backend logs the SMS body at INFO so the validator can
   scrape the 6-digit code).

Then make the deployment's stdout tailable from where you run
validate by picking ONE of the `--log-source` flavours:

- `file:/var/log/regstack.log` — tail a local file. Set up by
  routing the deployment's stdout to that path (`systemd:
  StandardOutput=append:/var/log/regstack.log`, `docker run …
  &> /var/log/regstack.log`).
- `ssh:user@host:/var/log/regstack.log` — tail a file on a remote
  host over SSH. Uses key-based auth (`BatchMode=yes`).
- `docker:<container>` — `docker logs -f --since 1s <container>`.
- `cmd:'journalctl -fu regstack.service'` — escape hatch for any
  command that streams the deployment's stdout to its own stdout.

### Options

- `--url URL` — base URL where the regstack JSON router is mounted
  (e.g. `https://host/api/auth`). **Required.**
- `--log-source SPEC` — see "Preparation" above.
- `--phone E.164` — phone number for the SMS 2FA probe. Without
  this, the SMS phase is skipped even on a deployment that has it
  mounted.
- `--probe-email-domain DOMAIN` — domain for the throwaway user
  (default `regstack-probe.example`, RFC 2606 reserved so no real
  mail can be sent by mistake).
- `--password PW` — explicit probe password (default: random
  32-byte urlsafe).
- `--skip oauth,sms,reset,account` — comma-separated phases to
  skip.
- `--json` — emit a JSON report instead of the human ✔/✘ stream.
- `--no-cleanup` — leave the probe user behind (debugging only).
- `-v`, `--verbose` — log every HTTP request and tailed log line.
- `--insecure` — skip TLS verification (self-signed staging
  certs).
- `--timeout SECS` — per-request HTTP timeout (default 10).

### Phases

| Phase | What it checks |
|-------|----------------|
| `reachability` | Base URL responds to `POST /login` with 401/422 (not 404 → wrong `--url`) |
| `features` | Which optional routers are mounted (oauth, admin, sms-2fa, password-reset) |
| `register` → `verify` → `login` → `/me` → `logout` → `blacklist` → `re-login` | Core auth chain |
| `patch /me` → `change-password` → `change-email` | Account management (bulk-revoke verified on each mutation) |
| `password-reset` | `/forgot-password` → reset → confirm old session revoked → re-login |
| `oauth:start` | `GET /oauth/google/start` returns a 302 to `accounts.google.com` with `client_id` + `state` (does not complete the flow) |
| `sms-2fa` | Full phone-setup → MFA-required login → confirm → disable flow |
| `cleanup` | `DELETE /account`; runs in a `finally` so it fires even on phase abort |

Each phase reports its own ✔ / ✘ line. The runner aborts after a
hard prerequisite (reachability or register) but otherwise keeps
going so one broken flow doesn't mask the rest.

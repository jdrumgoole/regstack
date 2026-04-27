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
uv run regstack init --target /etc/myapp --force
```

Options:

- `--target DIR` — directory to write the config files (default cwd).
- `--force` — overwrite without confirming.

Re-running the wizard prompts before overwriting unless `--force` is
passed; pre-existing answers aren't kept (the wizard is intentionally
stateless).

## `regstack create-admin`

Create or promote a superuser. Idempotent.

```bash
uv run regstack create-admin --email admin@example.com
uv run regstack create-admin --email admin@example.com --password 'long-strong-password'
uv run regstack create-admin --email admin@example.com --config /etc/myapp/regstack.toml
```

Options:

- `--email EMAIL` *(required)*.
- `--password PW` — if omitted, prompts (with confirmation).
- `--config PATH` — TOML file to load (default: env or cwd).

If the user already exists, the command sets `is_superuser=True` and
keeps the existing password. If they don't exist, it creates the user
with `is_active=True`, `is_verified=True`, `is_superuser=True` and the
provided password.

## `regstack doctor`

Runs read-only validation against the loaded config and reports each
check on a green/red line. Exit code is the number of failed checks —
suitable for use in container health checks.

```bash
uv run regstack doctor
uv run regstack doctor --config /etc/myapp/regstack.toml
uv run regstack doctor --check-dns
uv run regstack doctor --send-test-email me@example.com
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

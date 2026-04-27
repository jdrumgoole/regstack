# SQLite demo

Zero-infrastructure regstack: a single `.sqlite` file holds everything.
Useful for local development, CI smoke tests, and any deployment where
you don't want to run a database server alongside your app.

## Run

```bash
export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
uv run uvicorn examples.sqlite.main:app --reload
```

Then open <http://localhost:8000/account/login> in a browser, or
exercise the JSON API:

```bash
curl -X POST http://localhost:8000/api/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"alice@example.com","password":"hunter2hunter2","full_name":"Alice"}'
```

The verification link, MFA codes, and password-reset URLs are printed
to stdout (the demo registers hooks for that — real hosts would route
those events to email/SMS providers via `regstack.set_email_backend(...)`
/ `regstack.set_sms_backend(...)`).

## What's bundled

Same SSR pages, JSON API, admin router, and themed dashboard as the
other backend demos — the only difference is the `database_url` in
[`regstack.toml`](regstack.toml). Drop `regstack-demo.sqlite` between
runs to start clean.

# Postgres demo

regstack against a Postgres server via SQLAlchemy 2 + asyncpg.

## Pre-requisites

- A Postgres server you can reach.
- A database where the connecting user has CREATE TABLE permission.

```bash
createdb regstack_demo
```

## Run

```bash
export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
export REGSTACK_DATABASE_URL='postgresql+asyncpg://postgres@localhost/regstack_demo'
uv run uvicorn examples.postgres.main:app --reload
```

Then visit <http://localhost:8000/account/login>.

## What's bundled

Same SSR pages, JSON API, admin router, themed dashboard, and printer
hooks as the SQLite and Mongo demos. The only thing that changed is the
`database_url`. The SQL repo implementations are identical between
SQLite and Postgres — only the driver and dialect differ.

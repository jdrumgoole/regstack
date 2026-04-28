# Changelog

All notable changes to this project are documented here. The
authoritative copy lives at
[`docs/changelog.md`](docs/changelog.md) and is rendered into the
Sphinx docs.

## 0.2.1 — 2026-04-28

Hotfix for 0.2.0: `import regstack` failed on a base install because
several modules in the import path (`models/_objectid.py`,
`backends/protocols.py`, four routers, and the SQL `mfa_code_repo`)
had unconditional `from bson …` / `from regstack.backends.mongo …`
imports — but `pymongo` became an optional `mongo` extra in 0.2.0.
Added a CI smoketest that builds the wheel and imports it in a
no-extras venv, plus an in-process regression test that blocks `bson`
/ `pymongo` via `sys.meta_path`.

## 0.2.0 — 2026-04-28

Multi-backend support — SQLite (default), Postgres, MongoDB — switched
by `database_url` URL scheme. Bundled Alembic migrations for SQL
backends. Embedding API change: `RegStack(config=, db=)` →
`RegStack(config=, backend=None)`. README + core docs rewritten for
less-expert readers (problem framing, hyperlinks to external
standards, comparison vs Auth0/Clerk/Keycloak/fastapi-users).

See [`docs/changelog.md`](docs/changelog.md) for the full per-feature
breakdown.

## 0.1.1 — 2026-04-27

- Rewrite README relative links as absolute URLs so they resolve on the
  PyPI project page. README-only release.

## 0.1.0 — 2026-04-27

First tagged release. Bundles M1–M6 from the development plan into a
single Apache-2.0 package on PyPI.

See [`docs/changelog.md`](docs/changelog.md) for the per-milestone
breakdown of M1 through M6.

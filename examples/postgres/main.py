"""Postgres demo.

Run from the repo root:

    cd examples/postgres
    export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
    export REGSTACK_DATABASE_URL='postgresql+asyncpg://postgres@localhost/regstack_demo'
    uv run uvicorn examples.postgres.main:app --reload

You'll need a Postgres server reachable at the URL above with permission
to CREATE TABLE in the target database.
"""

from __future__ import annotations

from pathlib import Path

from regstack import RegStack, RegStackConfig

from examples._common.app import attach_demo_hooks, build_demo_app

_HERE = Path(__file__).parent
config = RegStackConfig.load(toml_path=_HERE / "regstack.toml")
regstack = RegStack(config=config)
attach_demo_hooks(regstack)
app = build_demo_app(
    config=config,
    regstack=regstack,
    title_suffix="postgres demo",
)

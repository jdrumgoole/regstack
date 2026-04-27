"""MongoDB demo.

Run from the repo root:

    cd examples/mongo
    export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
    export REGSTACK_DATABASE_URL=mongodb://localhost:27017/regstack_demo
    uv run uvicorn examples.mongo.main:app --reload

Needs a local Mongo on the URL above. Same JSON API + SSR pages as the
SQLite and Postgres demos — the only difference is the backend URL.
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
    branding_dir=_HERE / "branding",
    title_suffix="mongo demo",
)

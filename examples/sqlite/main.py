"""SQLite demo — zero infrastructure required.

Run from the repo root:

    cd examples/sqlite
    export REGSTACK_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(64))')
    uv run uvicorn examples.sqlite.main:app --reload

Then open http://localhost:8000/account/login.

The whole regstack feature set runs against a single .sqlite file in
this directory. No daemon, no service container. Every other demo
(`examples/postgres`, `examples/mongo`) shares the same FastAPI scaffold
and only swaps the URL.
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
    title_suffix="sqlite demo",
)

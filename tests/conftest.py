from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo import AsyncMongoClient

from regstack import RegStack, RegStackConfig
from regstack.auth.clock import FrozenClock
from regstack.config.schema import EmailConfig
from regstack.email.console import ConsoleEmailService

# Tests must be parallel-safe: each xdist worker gets its own DB so two
# workers running the same test never see each other's writes.
_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "gw0")


def _unique_db_name() -> str:
    return f"regstack_test_{_WORKER_ID}_{secrets.token_hex(4)}"


def _build_config(jwt_secret: str, db_name: str, **overrides: Any) -> RegStackConfig:
    base: dict[str, Any] = dict(
        toml_path=Path("/dev/null"),
        secrets_env_path=Path("/dev/null"),
        jwt_secret=jwt_secret,
        mongodb_database=db_name,
        mongodb_url="mongodb://localhost:27017",
        require_verification=False,
        allow_registration=True,
        rate_limit_disabled=True,
        email=EmailConfig(backend="console", from_address="test@example.com"),
    )
    base.update(overrides)
    return RegStackConfig.load(**base)


@pytest.fixture
def jwt_secret() -> str:
    return secrets.token_urlsafe(64)


@pytest.fixture
def db_name() -> str:
    return _unique_db_name()


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def config(jwt_secret: str, db_name: str) -> RegStackConfig:
    return _build_config(jwt_secret, db_name)


@pytest_asyncio.fixture
async def mongo_client(config: RegStackConfig) -> AsyncIterator[AsyncMongoClient]:
    from regstack.db.client import make_client

    client: AsyncMongoClient = make_client(config)
    try:
        yield client
    finally:
        await client.drop_database(config.mongodb_database)
        await client.aclose()


@pytest_asyncio.fixture
async def regstack(
    config: RegStackConfig,
    mongo_client: AsyncMongoClient,
    frozen_clock: FrozenClock,
) -> AsyncIterator[RegStack]:
    db = mongo_client[config.mongodb_database]
    rs = RegStack(
        config=config,
        db=db,
        clock=frozen_clock,
        email_service=ConsoleEmailService(),
    )
    await rs.install_indexes()
    yield rs


@pytest_asyncio.fixture
async def app(regstack: RegStack) -> FastAPI:
    app = FastAPI()
    app.include_router(regstack.router, prefix="/api/auth")
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def make_client(
    jwt_secret: str,
    db_name: str,
    mongo_client: AsyncMongoClient,
    frozen_clock: FrozenClock,
) -> Callable[..., Any]:
    """Factory yielding ``(regstack, AsyncClient)`` for tests that need a non-default config.

    Use as an async context manager:

        async with make_client(require_verification=True) as (rs, client):
            ...

    Re-uses the per-worker DB; ``install_indexes`` is idempotent so spawning
    a second RegStack on the same DB does not collide.
    """

    @asynccontextmanager
    async def _factory(**overrides: Any) -> AsyncIterator[tuple[RegStack, AsyncClient]]:
        cfg = _build_config(jwt_secret, db_name, **overrides)
        db = mongo_client[cfg.mongodb_database]
        rs = RegStack(
            config=cfg,
            db=db,
            clock=frozen_clock,
            email_service=ConsoleEmailService(),
        )
        await rs.install_indexes()
        app = FastAPI()
        app.include_router(rs.router, prefix="/api/auth")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield rs, ac

    return _factory

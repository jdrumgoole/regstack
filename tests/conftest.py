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

from regstack import RegStack, RegStackConfig
from regstack.auth.clock import FrozenClock
from regstack.config.schema import EmailConfig
from regstack.email.console import ConsoleEmailService

# Tests must be parallel-safe: each xdist worker gets its own DB so two
# workers running the same test never see each other's writes.
_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "gw0")

_BACKENDS_AVAILABLE: list[str] = ["sqlite", "mongo"]
if os.environ.get("REGSTACK_TEST_POSTGRES_URL"):
    _BACKENDS_AVAILABLE.append("postgres")


def _unique_token() -> str:
    return secrets.token_hex(4)


def _make_database_url(backend: str, token: str, *, file_dir: Path) -> tuple[str, str | None]:
    """Return (database_url, mongo_db_name_for_cleanup_or_None)."""
    if backend == "sqlite":
        path = file_dir / f"regstack-{_WORKER_ID}-{token}.sqlite"
        return f"sqlite+aiosqlite:///{path}", None
    if backend == "mongo":
        db_name = f"regstack_test_{_WORKER_ID}_{token}"
        return f"mongodb://localhost:27017/{db_name}", db_name
    if backend == "postgres":
        base = os.environ["REGSTACK_TEST_POSTGRES_URL"].rstrip("/")
        db_name = f"regstack_test_{_WORKER_ID}_{token}"
        return f"{base}/{db_name}", db_name
    raise ValueError(f"unknown backend: {backend}")


def _build_config(
    *,
    jwt_secret: str,
    database_url: str,
    mongo_db_name: str | None,
    **overrides: Any,
) -> RegStackConfig:
    base: dict[str, Any] = dict(
        toml_path=Path("/dev/null"),
        secrets_env_path=Path("/dev/null"),
        jwt_secret=jwt_secret,
        database_url=database_url,
        require_verification=False,
        allow_registration=True,
        rate_limit_disabled=True,
        email=EmailConfig(backend="console", from_address="test@example.com"),
    )
    if mongo_db_name is not None:
        base["mongodb_database"] = mongo_db_name
    base.update(overrides)
    return RegStackConfig.load(**base)


@pytest.fixture(params=_BACKENDS_AVAILABLE, ids=_BACKENDS_AVAILABLE)
def backend_kind(request) -> str:
    return request.param


@pytest.fixture
def jwt_secret() -> str:
    return secrets.token_urlsafe(64)


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def db_token() -> str:
    return _unique_token()


@pytest.fixture
def database_url(backend_kind: str, db_token: str, tmp_path: Path) -> tuple[str, str | None]:
    return _make_database_url(backend_kind, db_token, file_dir=tmp_path)


@pytest.fixture
def config(
    jwt_secret: str,
    database_url: tuple[str, str | None],
) -> RegStackConfig:
    url, mongo_db = database_url
    return _build_config(jwt_secret=jwt_secret, database_url=url, mongo_db_name=mongo_db)


@pytest_asyncio.fixture
async def regstack(
    config: RegStackConfig,
    backend_kind: str,
    frozen_clock: FrozenClock,
) -> AsyncIterator[RegStack]:
    rs = RegStack(
        config=config,
        clock=frozen_clock,
        email_service=ConsoleEmailService(),
    )
    await rs.install_schema()
    try:
        yield rs
    finally:
        if backend_kind == "mongo":
            from regstack.backends.mongo import MongoBackend

            assert isinstance(rs.backend, MongoBackend)
            await rs.backend.client.drop_database(config.mongodb_database)
        await rs.aclose()


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
    config: RegStackConfig,
    backend_kind: str,
    jwt_secret: str,
    database_url: tuple[str, str | None],
    frozen_clock: FrozenClock,
) -> Callable[..., Any]:
    """Factory yielding ``(regstack, AsyncClient)`` for tests that need a
    non-default config. Each call returns its own RegStack against the
    same per-test database URL.
    """

    @asynccontextmanager
    async def _factory(**overrides: Any) -> AsyncIterator[tuple[RegStack, AsyncClient]]:
        url, mongo_db = database_url
        cfg = _build_config(
            jwt_secret=jwt_secret,
            database_url=url,
            mongo_db_name=mongo_db,
            **overrides,
        )
        rs = RegStack(
            config=cfg,
            clock=frozen_clock,
            email_service=ConsoleEmailService(),
        )
        await rs.install_schema()
        try:
            app = FastAPI()
            app.include_router(rs.router, prefix="/api/auth")
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                yield rs, ac
        finally:
            await rs.aclose()

    return _factory


# Backwards-compat fixture for the few unit tests that still touch the
# raw Mongo client. These tests are mongo-only by definition.
@pytest_asyncio.fixture
async def mongo_client():
    from regstack.backends.mongo import make_client
    from regstack.config.schema import RegStackConfig as _Cfg

    db_name = f"regstack_legacy_{_WORKER_ID}_{_unique_token()}"
    cfg = _Cfg(
        jwt_secret=secrets.token_urlsafe(32),
        database_url=f"mongodb://localhost:27017/{db_name}",
        mongodb_database=db_name,
    )
    client = make_client(cfg)
    try:
        yield client
    finally:
        await client.drop_database(db_name)
        await client.aclose()

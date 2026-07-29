from __future__ import annotations

import importlib.util
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

_MONGO_URL = "mongodb://localhost:27017"

# --- Embedded SecantusDB ---------------------------------------------------
#
# SecantusDB speaks the MongoDB wire protocol, so the `secantus`
# parametrization drives the *same* Mongo repositories as `mongo` — the
# point is to catch a compatibility regression, not to test different code.
#
# One server per xdist worker, not one per test: ~950 tests would spend all
# their time on server startup. That mirrors the `mongo` shape (one server,
# a fresh database per test), except the server is worker-local, so nothing
# is shared across workers. `port=0` lets the kernel assign a free port —
# no fixed port to collide on — and `:memory:` storage means there are no
# files to clean up and no leftover databases for the sweep to find.
_secantus_server_instance: Any = None


def _secantus_uri() -> str:
    """Start this worker's embedded server on first use and return its URI."""
    global _secantus_server_instance
    if _secantus_server_instance is None:
        from secantus import SecantusDBServer

        server = SecantusDBServer(port=0, storage_path=":memory:")
        server.start()
        _secantus_server_instance = server
    # `server.uri` ends with a slash; callers append `/{db_name}`, and pymongo
    # rejects the resulting `//db` as a bad database name.
    return str(_secantus_server_instance.uri).rstrip("/")


def _stop_secantus_server() -> None:
    global _secantus_server_instance
    if _secantus_server_instance is not None:
        try:
            _secantus_server_instance.stop()
        finally:
            _secantus_server_instance = None


# Every Mongo DB created by this run carries the run token in its name so
# pytest_sessionfinish can sweep the whole run's leftovers — including DBs
# whose per-test teardown never ran (worker crash, -x abort). The token is
# minted once in the xdist controller and inherited by workers via env, and
# scopes the sweep to THIS run only: parallel test runs in other worktrees
# must never have their live DBs dropped out from under them.
_RUN_TOKEN_ENV = "REGSTACK_TEST_RUN_TOKEN"


def pytest_configure(config: Any) -> None:
    os.environ.setdefault(_RUN_TOKEN_ENV, secrets.token_hex(4))


def _run_token() -> str:
    return os.environ[_RUN_TOKEN_ENV]


def _mongo_db_prefix() -> str:
    return f"regstack_test_{_run_token()}_"


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    # Every process that started an embedded server owns stopping it —
    # workers included, and before the controller-only early return below.
    _stop_secantus_server()
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return  # workers exit first; the controller does the sweep
    if "mongo" not in _BACKENDS_AVAILABLE:
        return
    from pymongo import MongoClient

    prefix = _mongo_db_prefix()
    try:
        client: MongoClient[dict[str, Any]] = MongoClient(_MONGO_URL, serverSelectionTimeoutMS=2000)
        try:
            for name in client.list_database_names():
                if name.startswith(prefix):
                    client.drop_database(name)
        finally:
            client.close()
    except Exception as exc:  # a failed sweep must not mask test results
        print(f"\nwarning: could not sweep leftover test databases ({prefix}*): {exc}")


def _resolve_backends() -> list[str]:
    """Pick which backends the parametrized fixture covers.

    Override with ``REGSTACK_TEST_BACKENDS=sqlite,mongo,postgres,secantus``
    to constrain a run to specific backends — used by the per-backend
    invoke tasks (test-sqlite, test-mongo, test-postgres, test-secantus).

    Default: sqlite + mongo, plus secantus whenever the package is
    importable. Postgres joins automatically when
    ``REGSTACK_TEST_POSTGRES_URL`` is set.

    ``secantus`` needs no external service — it embeds its own server —
    so it joins the default set on any machine that has the wheel. An
    explicit override still wins: asking for a backend that isn't
    installed should fail loudly rather than silently shrink the matrix.
    """
    override = os.environ.get("REGSTACK_TEST_BACKENDS")
    if override:
        return [b.strip() for b in override.split(",") if b.strip()]
    backends = ["sqlite", "mongo"]
    if os.environ.get("REGSTACK_TEST_POSTGRES_URL"):
        backends.append("postgres")
    if importlib.util.find_spec("secantus") is not None:
        backends.append("secantus")
    return backends


_BACKENDS_AVAILABLE: list[str] = _resolve_backends()


def wire_protocol_backend() -> str:
    """Backend name for tests that need a MongoDB-wire server, either one.

    Used by test modules that override the parametrized ``backend_kind``
    to pin themselves off the SQL matrix. Hard-coding ``"mongo"`` there
    would make those tests demand a real mongod even in a secantus-only
    run — a connection error rather than an honest skip.
    """
    if "mongo" in _BACKENDS_AVAILABLE:
        return "mongo"
    if "secantus" in _BACKENDS_AVAILABLE:
        return "secantus"
    return "mongo"  # nothing active: let the caller's own skip logic speak


def _unique_token() -> str:
    return secrets.token_hex(4)


def _make_database_url(backend: str, token: str, *, file_dir: Path) -> tuple[str, str | None]:
    """Return (database_url, mongo_db_name_for_cleanup_or_None)."""
    if backend == "sqlite":
        path = file_dir / f"regstack-{_WORKER_ID}-{token}.sqlite"
        return f"sqlite+aiosqlite:///{path}", None
    if backend == "mongo":
        db_name = f"{_mongo_db_prefix()}{_WORKER_ID}_{token}"
        return f"{_MONGO_URL}/{db_name}", db_name
    if backend == "postgres":
        base = os.environ["REGSTACK_TEST_POSTGRES_URL"].rstrip("/")
        db_name = f"regstack_test_{_WORKER_ID}_{token}"
        return f"{base}/{db_name}", db_name
    if backend == "secantus":
        db_name = f"{_mongo_db_prefix()}{_WORKER_ID}_{token}"
        # No cleanup name returned: `:memory:` storage dies with the worker,
        # so the mongo sweep has nothing to reap. The db name still carries
        # the worker + token so concurrent tests can't collide inside the
        # one embedded server.
        return f"{_secantus_uri()}/{db_name}", db_name
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


@pytest_asyncio.fixture
async def _ensure_postgres_db(
    backend_kind: str, database_url: tuple[str, str | None]
) -> AsyncIterator[None]:
    """For Postgres, CREATE DATABASE before the test runs and DROP it after.

    SQLite creates files on demand; Mongo creates databases on first
    write; Postgres requires the DB to exist before you can connect.
    """
    if backend_kind != "postgres":
        yield
        return
    from urllib.parse import urlsplit, urlunsplit

    import asyncpg

    url, _ = database_url
    # url looks like postgresql+asyncpg://user:pw@host:port/dbname.
    # Strip the +asyncpg suffix and the database path so we can connect to
    # the server's "postgres" maintenance DB to issue CREATE/DROP DATABASE.
    bare = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    parts = urlsplit(bare)
    db_name = parts.path.lstrip("/")
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()
    try:
        yield
    finally:
        conn = await asyncpg.connect(admin_url)
        try:
            await conn.execute(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
            )
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await conn.close()


@pytest_asyncio.fixture
async def _ensure_mongo_db_dropped(
    backend_kind: str, database_url: tuple[str, str | None]
) -> AsyncIterator[None]:
    """Drop the per-test Mongo DB after the test, whoever created it.

    The drop lives here — not in the ``regstack`` fixture — because tests
    using only the ``make_client`` factory never construct that fixture,
    and their DBs used to leak. ``config`` depends on this, so every path
    that can touch the database is covered.

    Applies to ``secantus`` too. Its storage is ``:memory:``, so nothing
    survives the worker either way, but without the drop a full run would
    accumulate a database per test in the embedded server's heap.
    """
    yield
    if backend_kind not in ("mongo", "secantus"):
        return
    from pymongo import AsyncMongoClient

    url, db_name = database_url
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(url)
    try:
        await client.drop_database(db_name)
    finally:
        await client.aclose()


@pytest.fixture
def config(
    jwt_secret: str,
    database_url: tuple[str, str | None],
    _ensure_postgres_db,
    _ensure_mongo_db_dropped,
) -> RegStackConfig:
    url, mongo_db = database_url
    return _build_config(jwt_secret=jwt_secret, database_url=url, mongo_db_name=mongo_db)


@pytest_asyncio.fixture
async def regstack(
    config: RegStackConfig,
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
# raw Mongo client. These tests are wire-protocol tests, so they're
# satisfied by either server: real mongod when it's in the active backend
# set, otherwise this worker's embedded SecantusDB. Running them under
# `secantus` is the point — TTL indexes, unique constraints and the
# ObjectId guards are exactly where a compatibility regression would
# surface, and skipping them would make a secantus-only run look greener
# than it is. Only a run with neither server skips.
@pytest_asyncio.fixture
async def mongo_client():
    if "mongo" in _BACKENDS_AVAILABLE:
        base_url = _MONGO_URL
    elif "secantus" in _BACKENDS_AVAILABLE:
        base_url = _secantus_uri()
    else:
        pytest.skip(
            "no wire-protocol backend active "
            "(set REGSTACK_TEST_BACKENDS to include 'mongo' or 'secantus')"
        )
    from regstack.backends.mongo import make_client
    from regstack.config.schema import RegStackConfig as _Cfg

    db_name = f"{_mongo_db_prefix()}legacy_{_WORKER_ID}_{_unique_token()}"
    cfg = _Cfg(
        jwt_secret=secrets.token_urlsafe(32),
        database_url=f"{base_url}/{db_name}",
        mongodb_database=db_name,
    )
    client = make_client(cfg)
    try:
        yield client
    finally:
        await client.drop_database(db_name)
        await client.aclose()

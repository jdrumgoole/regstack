"""Tests for ``AuthDependencies.current_user_optional``.

Mirrors the happy_path test style — mounts a host endpoint backed by
``current_user_optional`` on the test app and exercises the three
branches: anonymous → None, valid token → user, malformed/expired
token → None (no 401).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from regstack import RegStack
from regstack.models.user import BaseUser

REGISTER = "/api/auth/register"
LOGIN = "/api/auth/login"

CREDS = {
    "email": "opt@example.com",
    "password": "hunter2hunter2",
    "full_name": "Opt In",
}


@pytest_asyncio.fixture
async def optional_client(regstack: RegStack) -> AsyncClient:
    """A test client whose host app exposes ``/who`` via
    ``current_user_optional`` so we can assert the dep's actual
    HTTP-surface behaviour instead of just unit-testing the callable.
    """
    app = FastAPI()
    app.include_router(regstack.router, prefix="/api/auth")

    @app.get("/who")
    async def who(user: BaseUser | None = Depends(regstack.deps.current_user_optional())):
        if user is None:
            return {"email": None}
        return {"email": user.email}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.mark.asyncio
async def test_anonymous_returns_none_no_401(optional_client: AsyncClient) -> None:
    r = await optional_client.get("/who")
    assert r.status_code == 200
    assert r.json() == {"email": None}


@pytest.mark.asyncio
async def test_valid_bearer_returns_user(optional_client: AsyncClient) -> None:
    await optional_client.post(REGISTER, json=CREDS)
    r = await optional_client.post(
        LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]}
    )
    token = r.json()["access_token"]

    r = await optional_client.get("/who", headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"email": CREDS["email"]}


@pytest.mark.asyncio
async def test_malformed_bearer_collapses_to_none(optional_client: AsyncClient) -> None:
    """An expired/garbled token must NOT 401 the optional endpoint —
    that defeats the whole point. Treat it as anonymous.
    """
    r = await optional_client.get("/who", headers={"authorization": "Bearer not-a-real-jwt"})
    assert r.status_code == 200
    assert r.json() == {"email": None}


@pytest.mark.asyncio
async def test_non_bearer_scheme_collapses_to_none(optional_client: AsyncClient) -> None:
    r = await optional_client.get("/who", headers={"authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 200
    assert r.json() == {"email": None}


@pytest.mark.asyncio
async def test_revoked_token_collapses_to_none(optional_client: AsyncClient) -> None:
    """Logout revokes the jti in the blacklist. An optional endpoint
    presented with a revoked token should see anonymous — not 401.
    """
    await optional_client.post(REGISTER, json=CREDS)
    r = await optional_client.post(
        LOGIN, json={"email": CREDS["email"], "password": CREDS["password"]}
    )
    token = r.json()["access_token"]
    await optional_client.post("/api/auth/logout", headers={"authorization": f"Bearer {token}"})

    r = await optional_client.get("/who", headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"email": None}

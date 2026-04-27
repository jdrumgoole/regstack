from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

LOGIN = "/account/login"
REGISTER = "/account/register"
ME = "/account/me"
VERIFY = "/account/verify"
FORGOT = "/account/forgot"
RESET = "/account/reset"
CONFIRM_EMAIL = "/account/confirm-email-change"
STATIC = "/regstack-static"


async def _mount_ui(make_client_factory, **overrides):
    """Helper: spin up an app with the JSON router AND the UI router."""
    return make_client_factory(enable_ui_router=True, **overrides)


@pytest.mark.asyncio
async def test_ui_router_unmounted_by_default(client) -> None:
    r = await client.get(LOGIN)
    # The default fixture doesn't mount the UI router or the static files.
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_ui_pages_render_when_enabled(make_client) -> None:
    async with make_client(enable_ui_router=True) as (rs, _client):
        # The make_client fixture mounts only the JSON router; we extend
        # the underlying app to mount the UI router + static files for this
        # test. Reach into the in-memory app and add the routes.
        # rs.router has been built; ui_router and static_files are also exposed.
        # Build a fresh client that includes all three mounts.
        app = FastAPI()
        app.include_router(rs.router, prefix=rs.config.api_prefix)
        app.include_router(rs.ui_router, prefix=rs.config.ui_prefix)
        app.mount(rs.config.static_prefix, rs.static_files)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ui_client:
            for path, marker in [
                (LOGIN, "Sign in"),
                (REGISTER, "Create your account"),
                (FORGOT, "Reset your password"),
                (RESET, "Choose a new password"),
                (VERIFY, "Confirming your email"),
                (CONFIRM_EMAIL, "Confirming your new email"),
                (ME, "Your account"),
            ]:
                r = await ui_client.get(path)
                assert r.status_code == 200, f"{path}: {r.status_code}"
                assert marker in r.text, f"{path} missing marker {marker!r}"
                assert 'data-rs-api="/api/auth"' in r.text
                assert 'data-rs-ui="/account"' in r.text
                # CSP-friendly: no inline <style> tags or style="" attributes.
                assert "<style" not in r.text
                # Templates reference the static stylesheet through <link>.
                assert "/regstack-static/css/core.css" in r.text


@pytest.mark.asyncio
async def test_static_files_serve_core_and_theme(make_client) -> None:
    async with make_client(enable_ui_router=True) as (rs, _client):
        app = FastAPI()
        app.mount(rs.config.static_prefix, rs.static_files)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as static_client:
            r = await static_client.get(f"{STATIC}/css/core.css")
            assert r.status_code == 200
            assert "rs-card" in r.text
            r = await static_client.get(f"{STATIC}/css/theme.css")
            assert r.status_code == 200
            assert "--rs-accent" in r.text
            r = await static_client.get(f"{STATIC}/js/regstack.js")
            assert r.status_code == 200
            assert "regstack.access_token" in r.text


@pytest.mark.asyncio
async def test_host_template_dir_overrides_login_page(make_client, tmp_path: Path) -> None:
    host_dir = tmp_path / "tpl"
    (host_dir / "auth").mkdir(parents=True)
    (host_dir / "auth" / "login.html").write_text("OVERRIDDEN-LOGIN-PAGE")

    async with make_client(enable_ui_router=True) as (rs, _client):
        rs.add_template_dir(host_dir)
        app = FastAPI()
        app.include_router(rs.ui_router, prefix=rs.config.ui_prefix)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ui_client:
            r = await ui_client.get(LOGIN)
            assert r.status_code == 200
            assert r.text == "OVERRIDDEN-LOGIN-PAGE"
            # A non-overridden page still works.
            r = await ui_client.get(REGISTER)
            assert r.status_code == 200
            assert "Create your account" in r.text


@pytest.mark.asyncio
async def test_theme_css_url_renders_link(make_client) -> None:
    async with make_client(
        enable_ui_router=True,
        theme_css_url="https://cdn.example.com/host-theme.css",
    ) as (rs, _client):
        app = FastAPI()
        app.include_router(rs.ui_router, prefix=rs.config.ui_prefix)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ui_client:
            r = await ui_client.get(LOGIN)
            assert r.status_code == 200
            assert "https://cdn.example.com/host-theme.css" in r.text

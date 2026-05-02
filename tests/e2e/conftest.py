"""End-to-end fixtures for the OAuth setup wizard.

Boots the wizard's FastAPI app on a free 127.0.0.1 port inside a
background uvicorn thread so Playwright can drive the SPA. No
pywebview process — Playwright loads the URL directly.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

# Skip the whole module gracefully when Playwright (or its browsers)
# aren't installed. The unit suite still runs.
playwright = pytest.importorskip("playwright.sync_api")

from regstack.wizard.oauth_google.routes import build_wizard_app  # noqa: E402
from regstack.wizard.oauth_google.server import make_wizard_server  # noqa: E402
from regstack.wizard.theme_designer.routes import build_designer_app  # noqa: E402
from regstack.wizard.theme_designer.server import make_designer_server  # noqa: E402


def _start_uvicorn_in_thread(app, host: str, port: int) -> tuple[object, object]:
    """Boot a uvicorn instance in a background thread; return (server, thread).

    Caller is responsible for setting `server.should_exit = True` and
    joining the thread on teardown.
    """
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    uv = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(uv.serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return uv, thread


def _wait_for_http(url: str, *, deadline_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=0.5)
            if r.status_code == 200:
                return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError(f"Server at {url} did not respond within {deadline_seconds}s")


@pytest.fixture
def wizard_target_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def wizard_server(wizard_target_dir: Path) -> Iterator[dict]:
    """Start the OAuth wizard server on a free port; yield URL + token + dir."""
    server_descriptor = make_wizard_server(target_dir=wizard_target_dir)
    app = build_wizard_app(server_descriptor.settings)
    uv, thread = _start_uvicorn_in_thread(app, server_descriptor.host, server_descriptor.port)
    base_url = f"http://{server_descriptor.host}:{server_descriptor.port}"
    try:
        _wait_for_http(f"{base_url}/?token={server_descriptor.launch_token}")
    except Exception:
        uv.should_exit = True
        thread.join(timeout=2)
        raise

    try:
        yield {
            "url": server_descriptor.url,
            "token": server_descriptor.launch_token,
            "target_dir": wizard_target_dir,
            "base_url": base_url,
        }
    finally:
        uv.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def designer_server(tmp_path: Path) -> Iterator[dict]:
    """Start the theme designer server on a free port; yield URL + token + dir."""
    server_descriptor = make_designer_server(target_dir=tmp_path)
    app = build_designer_app(server_descriptor.settings)
    uv, thread = _start_uvicorn_in_thread(app, server_descriptor.host, server_descriptor.port)
    base_url = f"http://{server_descriptor.host}:{server_descriptor.port}"
    try:
        _wait_for_http(f"{base_url}/?token={server_descriptor.launch_token}")
    except Exception:
        uv.should_exit = True
        thread.join(timeout=2)
        raise

    try:
        yield {
            "url": server_descriptor.url,
            "token": server_descriptor.launch_token,
            "target_dir": tmp_path,
            "base_url": base_url,
        }
    finally:
        uv.should_exit = True
        thread.join(timeout=5)

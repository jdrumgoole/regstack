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


@pytest.fixture
def wizard_target_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def wizard_server(wizard_target_dir: Path) -> Iterator[dict]:
    """Start the wizard server on a free port; yield URL + token + dir."""
    import uvicorn

    server_descriptor = make_wizard_server(target_dir=wizard_target_dir)
    app = build_wizard_app(server_descriptor.settings)
    config = uvicorn.Config(
        app,
        host=server_descriptor.host,
        port=server_descriptor.port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    uv = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(uv.serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # Poll until the server answers — bounded by 5 seconds.
    deadline = time.monotonic() + 5.0
    base_url = f"http://{server_descriptor.host}:{server_descriptor.port}"
    while time.monotonic() < deadline:
        try:
            r = httpx.get(
                f"{base_url}/?token={server_descriptor.launch_token}",
                timeout=0.5,
            )
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.05)
    else:
        uv.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("Wizard server did not come up in time")

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

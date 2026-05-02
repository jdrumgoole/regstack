"""TestClient-driven tests for the theme designer's FastAPI app."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from regstack.wizard.theme_designer.routes import (
    DesignerSettings,
    build_designer_app,
)
from regstack.wizard.theme_designer.writer import THEME_FILE

_TOKEN = "test-launch-token"


def _make_client(tmp_path: Path) -> TestClient:
    settings = DesignerSettings(
        target_dir=tmp_path,
        launch_token=_TOKEN,
        shutdown_event=asyncio.Event(),
    )
    app = build_designer_app(settings)
    client = TestClient(app)
    client.headers.update({"X-Wizard-Token": _TOKEN})
    return client


def _good_payload() -> dict:
    return {
        "light": {"--rs-accent": "#0d9488", "--rs-radius": "10"},
        "dark": {"--rs-accent": "#2dd4bf"},
    }


# ---------------------------------------------------------------------------
# Token enforcement
# ---------------------------------------------------------------------------


def test_index_requires_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.get("/", headers={})
    assert response.status_code == 401


def test_index_accepts_token_in_query(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.get(f"/?token={_TOKEN}", headers={})
    assert response.status_code == 200
    assert "designer-root" in response.text


def test_api_state_requires_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.get("/api/state", headers={"X-Wizard-Token": "wrong"})
    assert response.status_code == 401


def test_api_save_requires_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post(
        "/api/save",
        json=_good_payload(),
        headers={"X-Wizard-Token": "wrong"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# /api/state
# ---------------------------------------------------------------------------


def test_state_returns_schema_and_defaults(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    body = client.get("/api/state").json()
    assert body["filename"] == THEME_FILE
    assert "--rs-accent" in body["schema"]["color_vars"]
    assert "--rs-font-body" in body["schema"]["font_vars"]
    assert "--rs-radius" in body["schema"]["radius_vars"]
    assert body["defaults"]["light"]["--rs-accent"] == "#2563eb"
    assert body["defaults"]["dark"]["--rs-accent"] == "#60a5fa"
    assert body["existing"] == {"light": {}, "dark": {}}


def test_state_round_trips_existing_file(tmp_path: Path) -> None:
    """If the operator saved a theme earlier, the designer reloads
    those values rather than starting from defaults."""
    client = _make_client(tmp_path)
    # Save once.
    save_resp = client.post("/api/save", json=_good_payload())
    assert save_resp.status_code == 200, save_resp.text
    # Reload state.
    body = client.get("/api/state").json()
    assert body["existing"]["light"]["--rs-accent"] == "#0d9488"
    assert body["existing"]["dark"]["--rs-accent"] == "#2dd4bf"


# ---------------------------------------------------------------------------
# /api/save
# ---------------------------------------------------------------------------


def test_save_writes_file(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post("/api/save", json=_good_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["light_count"] == 2
    assert body["dark_count"] == 1
    assert (tmp_path / THEME_FILE).exists()
    text = (tmp_path / THEME_FILE).read_text()
    assert "--rs-accent: #0d9488;" in text
    assert "@media (prefers-color-scheme: dark)" in text


def test_save_validates_payload(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    bad = {"light": {"--rs-accent": "not-a-colour"}, "dark": {}}
    response = client.post("/api/save", json=bad)
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert any(e["field"] == "--rs-accent" for e in body["errors"])
    # Nothing landed on disk.
    assert not (tmp_path / THEME_FILE).exists()


def test_save_rejects_non_dict_scopes(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post("/api/save", json={"light": "oops", "dark": {}})
    assert response.status_code == 400


def test_save_handles_empty_dark_scope(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post("/api/save", json={"light": {"--rs-accent": "#000"}, "dark": {}})
    assert response.status_code == 200
    text = (tmp_path / THEME_FILE).read_text()
    assert "prefers-color-scheme" not in text


def test_save_attaches_scope_to_errors(tmp_path: Path) -> None:
    """Errors from the dark scope are tagged with scope='dark' so the
    SPA can highlight the right form section."""
    client = _make_client(tmp_path)
    response = client.post(
        "/api/save",
        json={"light": {}, "dark": {"--rs-accent": "not-valid"}},
    )
    assert response.status_code == 422
    errors = response.json()["errors"]
    dark_errs = [e for e in errors if e.get("scope") == "dark"]
    assert dark_errs
    assert dark_errs[0]["field"] == "--rs-accent"


# ---------------------------------------------------------------------------
# /api/done
# ---------------------------------------------------------------------------


def test_done_signals_shutdown_event(tmp_path: Path) -> None:
    settings = DesignerSettings(
        target_dir=tmp_path,
        launch_token=_TOKEN,
        shutdown_event=asyncio.Event(),
    )
    app = build_designer_app(settings)
    client = TestClient(app)
    client.headers.update({"X-Wizard-Token": _TOKEN})
    assert settings.shutdown_event.is_set() is False
    response = client.post("/api/done")
    assert response.status_code == 200
    assert settings.shutdown_event.is_set() is True

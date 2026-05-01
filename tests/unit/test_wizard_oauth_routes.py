"""Integration tests for the wizard's FastAPI app.

Drive the app directly through :class:`fastapi.testclient.TestClient`
— no uvicorn process, no pywebview. Covers:

- token enforcement on every endpoint
- the happy-path validate-then-write flow
- the unhappy path (bad creds → ``/api/write`` rejected with 422)
- the ``/api/done`` shutdown signal
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from regstack.wizard.oauth_google.routes import WizardSettings, build_wizard_app
from regstack.wizard.oauth_google.writer import (
    CONFIG_FILE,
    SECRETS_ENV_KEY,
    SECRETS_FILE,
)

_TOKEN = "test-launch-token"


def _make_client(tmp_path: Path) -> TestClient:
    settings = WizardSettings(
        target_dir=tmp_path,
        api_prefix="/api/auth",
        launch_token=_TOKEN,
        shutdown_event=asyncio.Event(),
        existing_base_url="http://localhost:8000",
    )
    app = build_wizard_app(settings)
    client = TestClient(app)
    client.headers.update({"X-Wizard-Token": _TOKEN})
    return client


def _good_payload() -> dict:
    return {
        "existing_oauth": False,
        "replace_existing": False,
        "base_url": "http://localhost:8000",
        "client_id": "12345-abc.apps.googleusercontent.com",
        "client_secret": "GOCSPX-secretvalue1234",
        "auto_link_verified_emails": False,
        "enforce_mfa_on_oauth_signin": False,
    }


# ---------------------------------------------------------------------------
# Token enforcement
# ---------------------------------------------------------------------------


def test_index_requires_token_in_query(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    # Override the default header — index uses query-string token.
    response = client.get("/", headers={})
    assert response.status_code == 401


def test_index_accepts_token_in_query(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.get(f"/?token={_TOKEN}", headers={})
    assert response.status_code == 200
    assert "wizard-root" in response.text


def test_api_state_requires_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.get("/api/state", headers={"X-Wizard-Token": "wrong"})
    assert response.status_code == 401


def test_api_state_with_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.get("/api/state")
    assert response.status_code == 200
    body = response.json()
    assert body["base_url"] == "http://localhost:8000"
    assert body["redirect_uri"].endswith("/oauth/google/callback")
    assert body["existing_oauth"] is False
    assert body["config_file"] == CONFIG_FILE
    assert body["secrets_file"] == SECRETS_FILE


def test_api_state_detects_existing_oauth(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[oauth]\ngoogle_client_id = "old.apps.googleusercontent.com"\n'
    )
    client = _make_client(tmp_path)
    response = client.get("/api/state")
    assert response.json()["existing_oauth"] is True


def test_api_validate_requires_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post(
        "/api/step/2/validate",
        json={"base_url": "x"},
        headers={"X-Wizard-Token": "wrong"},
    )
    assert response.status_code == 401


def test_api_write_requires_token(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post(
        "/api/write",
        json=_good_payload(),
        headers={"X-Wizard-Token": "wrong"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Step-by-step validation
# ---------------------------------------------------------------------------


def test_validate_step_happy_path(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post(
        "/api/step/2/validate",
        json={"base_url": "https://app.example.com"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["errors"] == []


def test_validate_step_returns_field_errors(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post(
        "/api/step/7/validate",
        json={"client_id": "bad", "client_secret": "x"},
    )
    body = response.json()
    assert body["ok"] is False
    fields = {e["field"] for e in body["errors"]}
    assert fields == {"client_id", "client_secret"}


def test_validate_step_out_of_range_returns_404(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post("/api/step/99/validate", json={})
    assert response.status_code == 404


def test_validate_step_handles_empty_body(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post("/api/step/0/validate")
    assert response.status_code == 200
    assert response.json()["ok"] is True


# ---------------------------------------------------------------------------
# Write flow
# ---------------------------------------------------------------------------


def test_write_happy_path(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    response = client.post("/api/write", json=_good_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["replaced_existing"] is False
    assert body["config_diff"] == "added [oauth] table"

    # Files actually landed on disk.
    cfg_text = (tmp_path / CONFIG_FILE).read_text()
    assert "enable_oauth = true" in cfg_text
    assert "12345-abc.apps.googleusercontent.com" in cfg_text
    secrets_text = (tmp_path / SECRETS_FILE).read_text()
    assert f"{SECRETS_ENV_KEY}=GOCSPX-secretvalue1234" in secrets_text


def test_write_rejects_bad_payload_with_422(tmp_path: Path) -> None:
    bad = {**_good_payload(), "client_id": "not-a-google-id"}
    client = _make_client(tmp_path)
    response = client.post("/api/write", json=bad)
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["jump_to"] == 7
    assert any(e["field"] == "client_id" for e in body["errors"])
    # Nothing got written.
    assert not (tmp_path / CONFIG_FILE).exists()
    assert not (tmp_path / SECRETS_FILE).exists()


def test_write_returns_replaced_existing_when_oauth_already_present(
    tmp_path: Path,
) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        '[oauth]\ngoogle_client_id = "old.apps.googleusercontent.com"\n'
    )
    client = _make_client(tmp_path)
    payload = {**_good_payload(), "existing_oauth": True, "replace_existing": True}
    response = client.post("/api/write", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["replaced_existing"] is True


# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------


def test_done_signals_shutdown_event(tmp_path: Path) -> None:
    settings = WizardSettings(
        target_dir=tmp_path,
        api_prefix="/api/auth",
        launch_token=_TOKEN,
        shutdown_event=asyncio.Event(),
    )
    app = build_wizard_app(settings)
    client = TestClient(app)
    client.headers.update({"X-Wizard-Token": _TOKEN})

    assert settings.shutdown_event.is_set() is False
    response = client.post("/api/done")
    assert response.status_code == 200
    assert settings.shutdown_event.is_set() is True

"""TestClient-driven tests for the SES wizard's FastAPI routes.

AWS calls are stubbed by replacing the module-level probe functions in
:mod:`regstack.wizard.ses._aws`; routes call them through the
``_aws`` namespace so monkeypatching there is the supported seam.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from regstack.wizard.ses import _aws
from regstack.wizard.ses.routes import WizardSettings, build_wizard_app

TOKEN = "test-token-abcdef"


def _make_app(tmp_path: Path) -> tuple[Any, WizardSettings]:
    settings = WizardSettings(
        target_dir=tmp_path,
        launch_token=TOKEN,
        shutdown_event=asyncio.Event(),
    )
    return build_wizard_app(settings), settings


def _headers() -> dict[str, str]:
    return {"X-Wizard-Token": TOKEN}


def test_get_root_requires_token(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    assert client.get("/").status_code == 401
    # Wrong token also rejected.
    assert client.get("/?token=wrong").status_code == 401
    # Correct token serves HTML.
    r = client.get(f"/?token={TOKEN}")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_api_state_returns_initial_snapshot(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/state", headers=_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["num_steps"] == 9
    assert body["existing_ses"] is False
    assert body["config_file"].endswith("regstack.toml")


def test_api_state_rejects_missing_token(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    assert client.get("/api/state").status_code == 401


def test_step_validate_passes_welcome(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/step/0/validate", headers=_headers(), json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_step_validate_rejects_unknown_step(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    assert client.post("/api/step/42/validate", headers=_headers(), json={}).status_code == 404


def test_credentials_step_calls_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_probe(**kwargs: Any) -> _aws.CredentialProbe:
        calls.append(kwargs)
        return _aws.CredentialProbe(ok=True, account_id="123456789012", arn="arn:aws:iam::...:root")

    monkeypatch.setattr(_aws, "probe_credentials", fake_probe)

    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/step/3/validate",
        headers=_headers(),
        json={"credential_source": "chain", "ses_region": "eu-west-1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["aws"]["credential_ok"] is True
    assert body["aws"]["account_id"] == "123456789012"
    assert len(calls) == 1
    assert calls[0]["region"] == "eu-west-1"


def test_credentials_step_blocks_on_aws_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_probe(**kwargs: Any) -> _aws.CredentialProbe:
        return _aws.CredentialProbe(
            ok=False, error="ExpiredToken: The security token included in the request is expired"
        )

    monkeypatch.setattr(_aws, "probe_credentials", fake_probe)

    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/step/3/validate",
        headers=_headers(),
        json={"credential_source": "chain", "ses_region": "eu-west-1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert any(e["field"] == "credential_source" for e in body["errors"])


def test_sender_step_blocks_on_unverified_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_probe(**kwargs: Any) -> _aws.IdentityProbe:
        return _aws.IdentityProbe(address_status="unknown", domain_status="unknown")

    monkeypatch.setattr(_aws, "probe_sender_identity", fake_probe)

    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/step/4/validate",
        headers=_headers(),
        json={
            "from_address": "noreply@example.com",
            "ses_region": "eu-west-1",
            "credential_source": "chain",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert any(e["field"] == "from_address" for e in body["errors"])


def test_sandbox_step_requires_attestation_when_in_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_probe(**kwargs: Any) -> _aws.SandboxProbe:
        return _aws.SandboxProbe(in_sandbox=True, detection="api")

    monkeypatch.setattr(_aws, "probe_sandbox_state", fake_probe)

    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/step/5/validate",
        headers=_headers(),
        json={"ses_region": "eu-west-1", "credential_source": "chain"},
    )
    body = r.json()
    assert body["ok"] is False
    assert any(e["field"] == "sandbox_attested" for e in body["errors"])

    # Now attest and re-submit.
    r = client.post(
        "/api/step/5/validate",
        headers=_headers(),
        json={
            "ses_region": "eu-west-1",
            "credential_source": "chain",
            "sandbox_attested": True,
        },
    )
    assert r.json()["ok"] is True


def test_sandbox_step_emits_warning_when_detection_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_probe(**kwargs: Any) -> _aws.SandboxProbe:
        return _aws.SandboxProbe(in_sandbox=False, detection="unknown", error="AccessDenied")

    monkeypatch.setattr(_aws, "probe_sandbox_state", fake_probe)

    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/step/5/validate",
        headers=_headers(),
        json={"ses_region": "eu-west-1", "credential_source": "chain"},
    )
    body = r.json()
    assert body["ok"] is True
    assert any("sandbox" in w["message"].lower() for w in body.get("warnings", []))


def test_test_send_step_skipped_when_flag_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def fake_send(**kwargs: Any) -> _aws.TestSendProbe:
        nonlocal called
        called = True
        return _aws.TestSendProbe(ok=True, message_id="x")

    monkeypatch.setattr(_aws, "send_test_email", fake_send)
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/step/6/validate",
        headers=_headers(),
        json={"skip_test_send": True, "from_address": "noreply@example.com"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert called is False


def test_write_persists_files(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/write",
        headers=_headers(),
        json={
            "ses_region": "eu-west-1",
            "from_address": "noreply@example.com",
            "credential_source": "chain",
            "sandbox_attested": True,
            "skip_test_send": True,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert (tmp_path / "regstack.toml").exists()


def test_write_rejects_bad_payload(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/write",
        headers=_headers(),
        json={
            # Missing ses_region → step 2 fails.
            "from_address": "noreply@example.com",
            "credential_source": "chain",
        },
    )
    assert r.status_code == 422
    body = r.json()
    assert body["jump_to"] == 2


def test_done_signals_shutdown(tmp_path: Path) -> None:
    app, settings = _make_app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/done", headers=_headers(), json={})
    assert r.status_code == 200
    assert settings.shutdown_event.is_set()

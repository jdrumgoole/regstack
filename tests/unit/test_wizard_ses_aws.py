"""Cover the SES-wizard AWS layer (``wizard/ses/_aws.py``) without touching
real AWS. A fake aioboto3 records the session kwargs and feeds each SES/STS
call a canned response (or makes it raise) so every probe's success AND
tolerant-failure branch is exercised.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# `_aws` guards its own aioboto3 import (sets it to None when absent), so this
# import is always safe. The probe tests below replace `_aws.aioboto3` with a
# fake, so they need no real aioboto3 — only `test_aws_available_*` does.
from regstack.wizard.ses import _aws

# --- fake aioboto3 ---------------------------------------------------------


class _ClientCtx:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def __aenter__(self) -> Any:
        return self._client

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Session:
    def __init__(self, client: Any, recorder: dict, **kwargs: Any) -> None:
        self._client = client
        recorder["session_kwargs"] = kwargs
        self._recorder = recorder

    def client(self, service: str, region_name: str | None = None) -> _ClientCtx:
        self._recorder.setdefault("clients", []).append((service, region_name))
        return _ClientCtx(self._client)


class _FakeAioboto3:
    def __init__(self, client: Any, recorder: dict) -> None:
        self._client = client
        self._recorder = recorder

    def Session(self, **kwargs: Any) -> _Session:  # noqa: N802 — match aioboto3 API
        return _Session(self._client, self._recorder, **kwargs)


def _install(monkeypatch: pytest.MonkeyPatch, client: Any) -> dict:
    recorder: dict = {}
    monkeypatch.setattr(_aws, "aioboto3", _FakeAioboto3(client, recorder))
    return recorder


# --- pure helpers ----------------------------------------------------------


def test_aws_available_true_with_extra() -> None:
    pytest.importorskip("aioboto3")
    assert _aws.aws_available() is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Success", "verified"),
        ("Pending", "pending"),
        ("Failed", "failed"),
        ("Whatever", "unknown"),
        (None, "unknown"),
    ],
)
def test_status_mapping(raw: object, expected: str) -> None:
    assert _aws._status(raw) == expected


def test_describe_caps_length_and_includes_type() -> None:
    msg = _aws._describe(ValueError("x" * 1000))
    assert msg.startswith("ValueError: ")
    assert len(msg) == 400


# --- probe_credentials -----------------------------------------------------


@pytest.mark.asyncio
async def test_probe_credentials_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_caller_identity = AsyncMock(
        return_value={"Account": "123456789012", "Arn": "arn:aws:iam::x:user/y"}
    )
    rec = _install(monkeypatch, client)

    probe = await _aws.probe_credentials(region="us-east-1", source="chain")

    assert probe.ok is True
    assert probe.account_id == "123456789012"
    assert probe.arn == "arn:aws:iam::x:user/y"
    assert rec["clients"] == [("sts", "us-east-1")]
    assert rec["session_kwargs"] == {}


@pytest.mark.asyncio
async def test_probe_credentials_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_caller_identity = AsyncMock(side_effect=RuntimeError("denied"))
    _install(monkeypatch, client)

    probe = await _aws.probe_credentials(region="us-east-1", source="chain")

    assert probe.ok is False
    assert "denied" in (probe.error or "")


@pytest.mark.asyncio
async def test_probe_credentials_explicit_passes_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_caller_identity = AsyncMock(return_value={"Account": "1", "Arn": "a"})
    rec = _install(monkeypatch, client)

    await _aws.probe_credentials(
        region="eu-west-1",
        source="explicit",
        access_key_id="AKIA",
        secret_access_key="shh",
    )

    assert rec["session_kwargs"] == {
        "aws_access_key_id": "AKIA",
        "aws_secret_access_key": "shh",
    }


@pytest.mark.asyncio
async def test_probe_credentials_profile_passes_name(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_caller_identity = AsyncMock(return_value={"Account": "1", "Arn": "a"})
    rec = _install(monkeypatch, client)

    await _aws.probe_credentials(region="eu-west-1", source="profile", profile="prod")

    assert rec["session_kwargs"] == {"profile_name": "prod"}


# --- probe_sender_identity -------------------------------------------------


@pytest.mark.asyncio
async def test_probe_sender_identity_maps_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_identity_verification_attributes = AsyncMock(
        return_value={
            "VerificationAttributes": {
                "noreply@example.com": {"VerificationStatus": "Success"},
                "example.com": {"VerificationStatus": "Pending"},
            }
        }
    )
    _install(monkeypatch, client)

    probe = await _aws.probe_sender_identity(
        region="us-east-1", from_address="noreply@example.com", source="chain"
    )

    assert probe.address_status == "verified"
    assert probe.domain_status == "pending"
    assert probe.error is None


@pytest.mark.asyncio
async def test_probe_sender_identity_missing_at_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.get_identity_verification_attributes = AsyncMock(
        side_effect=AssertionError("must not call AWS for a malformed address")
    )
    _install(monkeypatch, client)

    probe = await _aws.probe_sender_identity(
        region="us-east-1", from_address="not-an-email", source="chain"
    )

    assert probe.address_status == "unknown"
    assert probe.domain_status == "unknown"
    assert "missing '@'" in (probe.error or "")


@pytest.mark.asyncio
async def test_probe_sender_identity_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_identity_verification_attributes = AsyncMock(side_effect=RuntimeError("boom"))
    _install(monkeypatch, client)

    probe = await _aws.probe_sender_identity(
        region="us-east-1", from_address="a@b.com", source="chain"
    )

    assert probe.address_status == "unknown"
    assert "boom" in (probe.error or "")


# --- probe_sandbox_state ---------------------------------------------------


@pytest.mark.asyncio
async def test_probe_sandbox_state_api_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get_account = AsyncMock(return_value={"ProductionAccessEnabled": False})
    _install(monkeypatch, client)

    probe = await _aws.probe_sandbox_state(region="us-east-1", source="chain")

    assert probe.in_sandbox is True
    assert probe.detection == "api"


@pytest.mark.asyncio
async def test_probe_sandbox_state_quota_heuristic_when_get_account_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.get_account = AsyncMock(side_effect=RuntimeError("AccessDenied"))
    client.get_send_quota = AsyncMock(return_value={"Max24HourSend": 200.0, "MaxSendRate": 1.0})
    _install(monkeypatch, client)

    probe = await _aws.probe_sandbox_state(region="us-east-1", source="chain")

    assert probe.in_sandbox is True
    assert probe.detection == "quota_heuristic"


@pytest.mark.asyncio
async def test_probe_sandbox_state_quota_heuristic_graduated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.get_account = AsyncMock(return_value={})  # no ProductionAccessEnabled key
    client.get_send_quota = AsyncMock(return_value={"Max24HourSend": 50000.0, "MaxSendRate": 14.0})
    _install(monkeypatch, client)

    probe = await _aws.probe_sandbox_state(region="us-east-1", source="chain")

    assert probe.in_sandbox is False
    assert probe.detection == "quota_heuristic"


@pytest.mark.asyncio
async def test_probe_sandbox_state_total_failure_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.get_account = AsyncMock(side_effect=RuntimeError("denied"))
    client.get_send_quota = AsyncMock(side_effect=RuntimeError("also denied"))
    _install(monkeypatch, client)

    probe = await _aws.probe_sandbox_state(region="us-east-1", source="chain")

    assert probe.in_sandbox is False
    assert probe.detection == "unknown"
    assert "also denied" in (probe.error or "")


# --- send_test_email -------------------------------------------------------


@pytest.mark.asyncio
async def test_send_test_email_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.send_email = AsyncMock(return_value={"MessageId": "0000-abc"})
    rec = _install(monkeypatch, client)

    probe = await _aws.send_test_email(
        region="us-east-1",
        from_address="noreply@example.com",
        to_address="me@example.com",
        source="chain",
    )

    assert probe.ok is True
    assert probe.message_id == "0000-abc"
    assert rec["clients"] == [("ses", "us-east-1")]
    # The call shape SES expects.
    kwargs = client.send_email.call_args.kwargs
    assert kwargs["Source"] == "noreply@example.com"
    assert kwargs["Destination"] == {"ToAddresses": ["me@example.com"]}


@pytest.mark.asyncio
async def test_send_test_email_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.send_email = AsyncMock(side_effect=RuntimeError("MessageRejected"))
    _install(monkeypatch, client)

    probe = await _aws.send_test_email(
        region="us-east-1",
        from_address="noreply@example.com",
        to_address="me@example.com",
        source="chain",
    )

    assert probe.ok is False
    assert "MessageRejected" in (probe.error or "")

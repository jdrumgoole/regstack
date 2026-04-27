from __future__ import annotations

import builtins

import pytest

from regstack.config.schema import SmsConfig
from regstack.sms.base import SmsMessage, is_valid_e164
from regstack.sms.factory import build_sms_service
from regstack.sms.null import NullSmsService


def test_e164_validator() -> None:
    assert is_valid_e164("+14155552671")
    assert is_valid_e164("+447911123456")
    assert not is_valid_e164("14155552671")  # missing +
    assert not is_valid_e164("+0123456789")  # leading zero in country code
    assert not is_valid_e164("+")
    assert not is_valid_e164("")
    assert not is_valid_e164("+abc")
    # Too long (>16 chars total)
    assert not is_valid_e164("+1234567890123456")


def test_factory_returns_null_by_default() -> None:
    service = build_sms_service(SmsConfig(backend="null"))
    assert isinstance(service, NullSmsService)


def test_factory_unknown_backend_raises() -> None:
    cfg = SmsConfig(backend="null")
    cfg.backend = "made-up"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="Unknown SMS backend"):
        build_sms_service(cfg)


@pytest.mark.asyncio
async def test_null_service_captures_outbox() -> None:
    service = NullSmsService()
    await service.send(SmsMessage(to="+14155552671", body="hello", from_number="MyApp"))
    assert len(service.outbox) == 1
    msg = service.outbox[0]
    assert msg.to == "+14155552671"
    assert msg.body == "hello"
    assert msg.from_number == "MyApp"


def test_sns_requires_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "aioboto3":
            raise ImportError("simulated missing aioboto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from regstack.sms.sns import SnsSmsService

    with pytest.raises(RuntimeError, match="sns' extra"):
        SnsSmsService(SmsConfig(backend="sns"))


def test_twilio_requires_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "twilio.rest" or name == "twilio":
            raise ImportError("simulated missing twilio")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from regstack.sms.twilio import TwilioSmsService

    with pytest.raises(RuntimeError, match="twilio' extra"):
        TwilioSmsService(SmsConfig(backend="twilio"))

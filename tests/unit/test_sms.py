from __future__ import annotations

import builtins
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

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


def test_factory_builds_sns() -> None:
    pytest.importorskip("aioboto3")
    from regstack.sms.sns import SnsSmsService

    svc = build_sms_service(SmsConfig(backend="sns", sns_region="us-east-1"))
    assert isinstance(svc, SnsSmsService)


def test_factory_builds_twilio() -> None:
    pytest.importorskip("twilio")
    from regstack.sms.twilio import TwilioSmsService

    svc = build_sms_service(
        SmsConfig(
            backend="twilio",
            twilio_account_sid="AC123",
            twilio_auth_token=SecretStr("tok"),
            from_number="+15550000000",
        )
    )
    assert isinstance(svc, TwilioSmsService)


@pytest.mark.asyncio
async def test_null_service_captures_outbox() -> None:
    service = NullSmsService()
    await service.send(SmsMessage(to="+14155552671", body="hello", from_number="MyApp"))
    assert len(service.outbox) == 1
    msg = service.outbox[0]
    assert msg.to == "+14155552671"
    assert msg.body == "hello"
    assert msg.from_number == "MyApp"


@pytest.mark.asyncio
async def test_null_service_log_bodies_true_logs_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="regstack.sms.null")
    service = NullSmsService(log_bodies=True)
    await service.send(SmsMessage(to="+14155552671", body="code 123456"))
    assert any("code 123456" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_null_service_log_bodies_false_suppresses_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="regstack.sms.null")
    service = NullSmsService(log_bodies=False)
    await service.send(SmsMessage(to="+14155552671", body="code 123456"))
    assert not any("code 123456" in r.getMessage() for r in caplog.records)
    assert any("body suppressed" in r.getMessage() for r in caplog.records)


def test_factory_threads_log_bodies_flag() -> None:
    service = build_sms_service(SmsConfig(backend="null", log_bodies=False))
    assert isinstance(service, NullSmsService)
    assert service._log_bodies is False


def test_log_bodies_defaults_off_for_safety() -> None:
    """Regression for the 2026-05-19 security review: the null backend
    must NOT log MFA codes by default. A misconfigured deployment that
    leaves the null backend in place shouldn't leak codes into shared
    logs. Symmetric with ``email.log_bodies`` (also False by default)."""
    assert SmsConfig().log_bodies is False
    assert NullSmsService()._log_bodies is False
    # The factory must thread the secure default through, too.
    assert build_sms_service(SmsConfig(backend="null"))._log_bodies is False  # type: ignore[attr-defined]


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


# --- SNS backend send() path -----------------------------------------------


def _fake_sns_session(captured: dict) -> object:
    """Build a fake aioboto3.Session whose sns client records publish().

    Captures the real ``Session`` up front so the replacement can still
    build a genuine instance without recursing into the monkeypatch.
    """
    import aioboto3

    real_session = aioboto3.Session

    class _Ctx:
        async def __aenter__(self):
            client = MagicMock()

            async def publish(**kwargs):
                captured["publish"] = kwargs
                return {"MessageId": "sns-1"}

            client.publish = publish
            return client

        async def __aexit__(self, *exc):
            return False

    def fake_session_cls(**kwargs):
        sess = real_session()
        client_mock = MagicMock(return_value=_Ctx())
        sess.client = client_mock  # type: ignore[method-assign]
        captured["client_mock"] = client_mock
        return sess

    return fake_session_cls


@pytest.mark.asyncio
async def test_sns_send_publishes_with_sender_id(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("aioboto3")
    import aioboto3

    captured: dict = {}
    monkeypatch.setattr(aioboto3, "Session", _fake_sns_session(captured))

    from regstack.sms.sns import SnsSmsService

    svc = SnsSmsService(SmsConfig(backend="sns", sns_region="us-east-1"))
    await svc.send(SmsMessage(to="+14155552671", body="hi", from_number="MyApp"))

    assert captured["publish"]["PhoneNumber"] == "+14155552671"
    assert captured["publish"]["Message"] == "hi"
    sender = captured["publish"]["MessageAttributes"]["AWS.SNS.SMS.SenderID"]
    assert sender == {"DataType": "String", "StringValue": "MyApp"}
    # The configured region must flow through to the sns client.
    assert captured["client_mock"].call_args.kwargs["region_name"] == "us-east-1"


@pytest.mark.asyncio
async def test_sns_send_without_sender_id_omits_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("aioboto3")
    import aioboto3

    captured: dict = {}
    monkeypatch.setattr(aioboto3, "Session", _fake_sns_session(captured))

    from regstack.sms.sns import SnsSmsService

    svc = SnsSmsService(SmsConfig(backend="sns"))
    await svc.send(SmsMessage(to="+14155552671", body="no sender"))

    assert "MessageAttributes" not in captured["publish"]


# --- Twilio backend: init validation + send() path -------------------------


def test_twilio_init_requires_sid_and_token() -> None:
    with pytest.raises(ValueError, match="twilio_account_sid and twilio_auth_token"):
        twilio_service_cls = _twilio_cls()
        twilio_service_cls(SmsConfig(backend="twilio", from_number="+15550000000"))


def test_twilio_init_requires_from_number() -> None:
    twilio_service_cls = _twilio_cls()
    with pytest.raises(ValueError, match="from_number"):
        twilio_service_cls(
            SmsConfig(
                backend="twilio",
                twilio_account_sid="AC123",
                twilio_auth_token=SecretStr("tok"),
            )
        )


def _twilio_cls():
    pytest.importorskip("twilio")
    from regstack.sms.twilio import TwilioSmsService

    return TwilioSmsService


@pytest.mark.asyncio
async def test_twilio_send_creates_message(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("twilio")
    import twilio.rest

    created: dict = {}

    class _FakeMessages:
        def create(self, **kwargs):
            created.update(kwargs)
            return MagicMock(sid="SM1")

    class _FakeClient:
        def __init__(self, sid, token):
            created["_auth"] = (sid, token)
            self.messages = _FakeMessages()

    monkeypatch.setattr(twilio.rest, "Client", _FakeClient)

    twilio_service_cls = _twilio_cls()
    svc = twilio_service_cls(
        SmsConfig(
            backend="twilio",
            twilio_account_sid="AC123",
            twilio_auth_token=SecretStr("tok"),
            from_number="+15550000000",
        )
    )
    # message.from_number is None, so the configured from_number is used.
    await svc.send(SmsMessage(to="+14155552671", body="hello"))

    assert created["to"] == "+14155552671"
    assert created["from_"] == "+15550000000"
    assert created["body"] == "hello"
    # The secret is unwrapped before reaching the SDK.
    assert created["_auth"] == ("AC123", "tok")


@pytest.mark.asyncio
async def test_twilio_send_prefers_message_from_number(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("twilio")
    import twilio.rest

    created: dict = {}

    class _FakeClient:
        def __init__(self, sid, token):
            self.messages = MagicMock()
            self.messages.create = lambda **kw: created.update(kw) or MagicMock(sid="SM2")

    monkeypatch.setattr(twilio.rest, "Client", _FakeClient)

    twilio_service_cls = _twilio_cls()
    svc = twilio_service_cls(
        SmsConfig(
            backend="twilio",
            twilio_account_sid="AC123",
            twilio_auth_token=SecretStr("tok"),
            from_number="+15550000000",
        )
    )
    await svc.send(SmsMessage(to="+14155552671", body="hi", from_number="+15559999999"))

    assert created["from_"] == "+15559999999"

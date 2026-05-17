from __future__ import annotations

import builtins
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr, ValidationError

from regstack.config.schema import EmailConfig
from regstack.email.base import EmailMessage


def test_ses_requires_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without aioboto3 installed, instantiating SesEmailService must fail
    with a friendly error, not an obscure ImportError at first send().
    """
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "aioboto3":
            raise ImportError("simulated missing aioboto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from regstack.email.ses import SesEmailService

    with pytest.raises(RuntimeError, match="ses' extra"):
        SesEmailService(EmailConfig(backend="ses"))


# --- EmailConfig validation for the explicit-creds pair ----------------


def test_ses_creds_must_be_paired_key_without_secret_rejected() -> None:
    with pytest.raises(ValidationError, match="must be set together"):
        EmailConfig(
            backend="ses",
            ses_access_key_id=SecretStr("AKIAEXAMPLE"),
        )


def test_ses_creds_must_be_paired_secret_without_key_rejected() -> None:
    with pytest.raises(ValidationError, match="must be set together"):
        EmailConfig(
            backend="ses",
            ses_secret_access_key=SecretStr("super-secret"),
        )


def test_ses_explicit_creds_and_profile_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        EmailConfig(
            backend="ses",
            ses_profile="my-profile",
            ses_access_key_id=SecretStr("AKIAEXAMPLE"),
            ses_secret_access_key=SecretStr("super-secret"),
        )


def test_ses_both_creds_unset_is_valid() -> None:
    cfg = EmailConfig(backend="ses")
    assert cfg.ses_access_key_id is None
    assert cfg.ses_secret_access_key is None


def test_ses_both_creds_set_is_valid() -> None:
    cfg = EmailConfig(
        backend="ses",
        ses_access_key_id=SecretStr("AKIAEXAMPLE"),
        ses_secret_access_key=SecretStr("super-secret"),
    )
    assert cfg.ses_access_key_id.get_secret_value() == "AKIAEXAMPLE"
    assert cfg.ses_secret_access_key.get_secret_value() == "super-secret"


def test_ses_profile_alone_is_valid() -> None:
    cfg = EmailConfig(backend="ses", ses_profile="my-profile")
    assert cfg.ses_profile == "my-profile"


# --- Session-kwarg wiring ------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_creds_passed_to_aioboto3_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explicit access_key / secret_access_key must flow through
    to ``aioboto3.Session(...)`` — boto3's resolution order is
    surprising and silent, so we pin the call shape.
    """
    pytest.importorskip("aioboto3")
    import aioboto3

    captured_kwargs: dict = {}

    real_session = aioboto3.Session

    def fake_session_cls(**kwargs):
        captured_kwargs.update(kwargs)
        # Hand back a session whose .client() yields a no-op SES stub.
        sess = real_session()

        class _Ctx:
            async def __aenter__(self):
                client = MagicMock()
                client.send_email = AsyncMock(return_value={"MessageId": "x"})
                return client

            async def __aexit__(self, *exc):
                return False

        sess.client = MagicMock(return_value=_Ctx())  # type: ignore[method-assign]
        return sess

    monkeypatch.setattr(aioboto3, "Session", fake_session_cls)

    from regstack.email.ses import SesEmailService

    svc = SesEmailService(
        EmailConfig(
            backend="ses",
            ses_region="us-east-2",
            ses_access_key_id=SecretStr("AKIAEXAMPLE"),
            ses_secret_access_key=SecretStr("super-secret"),
        )
    )
    msg = EmailMessage(
        to="x@example.com",
        subject="Hi",
        html="<p>body</p>",
        text="body",
        from_address="noreply@example.com",
        from_name="Noreply",
    )
    await svc.send(msg)

    assert captured_kwargs == {
        "aws_access_key_id": "AKIAEXAMPLE",
        "aws_secret_access_key": "super-secret",
    }


@pytest.mark.asyncio
async def test_no_explicit_creds_yields_empty_session_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default config (no profile, no explicit creds) must leave the
    boto3 credential chain untouched — i.e. an empty session kwarg
    dict, so env vars / instance metadata / shared file all still work.
    """
    pytest.importorskip("aioboto3")
    import aioboto3

    captured_kwargs: dict = {}
    real_session = aioboto3.Session

    def fake_session_cls(**kwargs):
        captured_kwargs.update(kwargs)
        sess = real_session()

        class _Ctx:
            async def __aenter__(self):
                client = MagicMock()
                client.send_email = AsyncMock(return_value={"MessageId": "x"})
                return client

            async def __aexit__(self, *exc):
                return False

        sess.client = MagicMock(return_value=_Ctx())  # type: ignore[method-assign]
        return sess

    monkeypatch.setattr(aioboto3, "Session", fake_session_cls)

    from regstack.email.ses import SesEmailService

    svc = SesEmailService(EmailConfig(backend="ses"))
    msg = EmailMessage(
        to="x@example.com",
        subject="Hi",
        html="<p>body</p>",
        text="body",
        from_address="noreply@example.com",
        from_name="Noreply",
    )
    await svc.send(msg)

    assert captured_kwargs == {}


@pytest.mark.asyncio
async def test_profile_only_passes_profile_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("aioboto3")
    import aioboto3

    captured_kwargs: dict = {}
    real_session = aioboto3.Session

    def fake_session_cls(**kwargs):
        captured_kwargs.update(kwargs)
        sess = real_session()

        class _Ctx:
            async def __aenter__(self):
                client = MagicMock()
                client.send_email = AsyncMock(return_value={"MessageId": "x"})
                return client

            async def __aexit__(self, *exc):
                return False

        sess.client = MagicMock(return_value=_Ctx())  # type: ignore[method-assign]
        return sess

    monkeypatch.setattr(aioboto3, "Session", fake_session_cls)

    from regstack.email.ses import SesEmailService

    svc = SesEmailService(EmailConfig(backend="ses", ses_profile="prod-mail"))
    await svc.send(
        EmailMessage(
            to="x@example.com",
            subject="Hi",
            html="<p>body</p>",
            text="body",
            from_address="noreply@example.com",
            from_name="Noreply",
        )
    )

    assert captured_kwargs == {"profile_name": "prod-mail"}

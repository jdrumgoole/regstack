from __future__ import annotations

import logging

import pytest

from regstack.config.schema import EmailConfig
from regstack.email.base import EmailMessage
from regstack.email.console import ConsoleEmailService
from regstack.email.factory import build_email_service


def _msg() -> EmailMessage:
    return EmailMessage(
        to="alice@example.com",
        subject="hi",
        html="<p>visit https://example.com/verify?token=ABC123</p>",
        text="visit https://example.com/verify?token=ABC123",
        from_address="noreply@example.com",
        from_name="RegStack",
    )


@pytest.mark.asyncio
async def test_console_default_logs_body_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="regstack.email.console")
    service = ConsoleEmailService()
    await service.send(_msg())
    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("token=ABC123" in r.getMessage() for r in debug_records)
    # And NOT at INFO
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert not any("token=ABC123" in r.getMessage() for r in info_records)


@pytest.mark.asyncio
async def test_console_log_bodies_true_promotes_body_to_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="regstack.email.console")
    service = ConsoleEmailService(log_bodies=True)
    await service.send(_msg())
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("token=ABC123" in r.getMessage() for r in info_records)


def test_factory_threads_log_bodies_flag() -> None:
    service = build_email_service(EmailConfig(backend="console", log_bodies=True))
    assert isinstance(service, ConsoleEmailService)
    assert service._body_level == logging.INFO

    default = build_email_service(EmailConfig(backend="console"))
    assert isinstance(default, ConsoleEmailService)
    assert default._body_level == logging.DEBUG

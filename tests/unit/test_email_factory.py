"""Cover ``build_email_service`` backend dispatch — each branch returns
the right service type (or raises on an unknown backend). The SES/SMTP
service internals are tested in their own files; here we only pin the
factory's routing.
"""

from __future__ import annotations

import pytest

from regstack.config.schema import EmailConfig
from regstack.email.console import ConsoleEmailService
from regstack.email.factory import build_email_service


def test_factory_builds_console() -> None:
    svc = build_email_service(EmailConfig(backend="console"))
    assert isinstance(svc, ConsoleEmailService)


def test_factory_builds_smtp() -> None:
    from regstack.email.smtp import SmtpEmailService

    svc = build_email_service(EmailConfig(backend="smtp", smtp_host="smtp.example.com"))
    assert isinstance(svc, SmtpEmailService)


def test_factory_builds_ses() -> None:
    pytest.importorskip("aioboto3")
    from regstack.email.ses import SesEmailService

    svc = build_email_service(EmailConfig(backend="ses"))
    assert isinstance(svc, SesEmailService)


def test_factory_unknown_backend_raises() -> None:
    cfg = EmailConfig(backend="console")
    cfg.backend = "carrier-pigeon"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="Unknown email backend"):
        build_email_service(cfg)

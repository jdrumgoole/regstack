from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.email.base import EmailService
from regstack.email.console import ConsoleEmailService

if TYPE_CHECKING:
    from regstack.config.schema import EmailConfig


def build_email_service(config: EmailConfig) -> EmailService:
    if config.backend == "console":
        return ConsoleEmailService()
    if config.backend == "smtp":
        from regstack.email.smtp import SmtpEmailService

        return SmtpEmailService(config)
    if config.backend == "ses":
        from regstack.email.ses import SesEmailService

        return SesEmailService(config)
    raise ValueError(f"Unknown email backend: {config.backend!r}")

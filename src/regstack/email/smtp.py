from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from typing import TYPE_CHECKING

import aiosmtplib

from regstack.email.base import EmailMessage, EmailService

if TYPE_CHECKING:
    from regstack.config.schema import EmailConfig


class SmtpEmailService(EmailService):
    """Sends mail via aiosmtplib. STARTTLS is enabled by default; set
    ``smtp_starttls=False`` for an SMTP-over-SSL server (port 465 et al.)
    or for plaintext local relays.
    """

    def __init__(self, config: EmailConfig) -> None:
        if not config.smtp_host:
            raise ValueError("EmailConfig.smtp_host is required for the SMTP backend.")
        self._config = config

    async def send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = message.from_header
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")

        username = self._config.smtp_username or None
        password = (
            self._config.smtp_password.get_secret_value()
            if self._config.smtp_password is not None
            else None
        )
        await aiosmtplib.send(
            mime,
            hostname=self._config.smtp_host,
            port=self._config.smtp_port,
            start_tls=self._config.smtp_starttls,
            username=username,
            password=password,
        )

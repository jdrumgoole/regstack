from __future__ import annotations

import logging

from regstack.email.base import EmailMessage, EmailService

log = logging.getLogger("regstack.email.console")


class ConsoleEmailService(EmailService):
    """Logs the email payload instead of sending it. Used in dev and tests.

    Captured messages are also kept in ``self.outbox`` so tests can assert on
    rendered content without scraping logs.
    """

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)
        log.info(
            "[regstack/console-email] To: %s | From: %s | Subject: %s",
            message.to,
            message.from_header,
            message.subject,
        )
        log.debug("[regstack/console-email] text body:\n%s", message.text)

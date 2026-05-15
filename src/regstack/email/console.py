from __future__ import annotations

import logging

from regstack.email.base import EmailMessage, EmailService

log = logging.getLogger("regstack.email.console")


class ConsoleEmailService(EmailService):
    """Logs the email payload instead of sending it. Used in dev and tests.

    Captured messages are also kept in ``self.outbox`` so tests can assert on
    rendered content without scraping logs.

    When ``log_bodies=True`` the rendered text body is logged at INFO so
    operators (and ``regstack validate``) can scrape one-time tokens from
    stdout without enabling DEBUG globally. Default is False — bodies stay
    at DEBUG so logs aren't noisy by default.
    """

    def __init__(self, *, log_bodies: bool = False) -> None:
        self.outbox: list[EmailMessage] = []
        self._body_level = logging.INFO if log_bodies else logging.DEBUG

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)
        log.info(
            "[regstack/console-email] To: %s | From: %s | Subject: %s",
            message.to,
            message.from_header,
            message.subject,
        )
        log.log(self._body_level, "[regstack/console-email] text body:\n%s", message.text)

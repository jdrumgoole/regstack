from __future__ import annotations

import logging

from regstack.sms.base import SmsMessage, SmsService

log = logging.getLogger("regstack.sms.null")


class NullSmsService(SmsService):
    """Default backend. Records messages in ``self.outbox`` so tests and dev
    runs can inspect them without contacting a real SMS gateway. Logs each
    send at INFO so the demo can grep the code out of stdout.
    """

    def __init__(self) -> None:
        self.outbox: list[SmsMessage] = []

    async def send(self, message: SmsMessage) -> None:
        self.outbox.append(message)
        log.info(
            "[regstack/null-sms] To: %s | From: %s | Body: %s",
            message.to,
            message.from_number or "(unset)",
            message.body,
        )

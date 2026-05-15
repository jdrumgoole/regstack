from __future__ import annotations

import logging

from regstack.sms.base import SmsMessage, SmsService

log = logging.getLogger("regstack.sms.null")


class NullSmsService(SmsService):
    """Default backend. Records messages in ``self.outbox`` so tests and dev
    runs can inspect them without contacting a real SMS gateway. Logs each
    send at INFO so the demo can grep the code out of stdout.

    ``log_bodies`` (default True) controls whether the message body
    (containing the 6-digit code) is included in the log line. Operators
    in shared environments who want call-only audit lines can flip it off.
    """

    def __init__(self, *, log_bodies: bool = True) -> None:
        self.outbox: list[SmsMessage] = []
        self._log_bodies = log_bodies

    async def send(self, message: SmsMessage) -> None:
        self.outbox.append(message)
        if self._log_bodies:
            log.info(
                "[regstack/null-sms] To: %s | From: %s | Body: %s",
                message.to,
                message.from_number or "(unset)",
                message.body,
            )
        else:
            log.info(
                "[regstack/null-sms] To: %s | From: %s | (body suppressed)",
                message.to,
                message.from_number or "(unset)",
            )

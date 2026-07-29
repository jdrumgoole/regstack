from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from regstack.sms.base import SmsMessage, SmsService

if TYPE_CHECKING:
    from regstack.config.schema import SmsConfig


class TwilioSmsService(SmsService):
    """Twilio Programmable Messaging backend. Requires the optional
    ``twilio`` extra (``pip install regstack[twilio]``).

    The Twilio Python SDK is sync; we hand off to a worker thread so we
    don't block the event loop.
    """

    def __init__(self, config: SmsConfig) -> None:
        try:
            from twilio.rest import Client  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "The Twilio SMS backend requires the 'twilio' extra. "
                "Install with `pip install regstack[twilio]` or `uv sync --extra twilio`."
            ) from exc
        if not (config.twilio_account_sid and config.twilio_auth_token):
            raise ValueError("Twilio backend needs both twilio_account_sid and twilio_auth_token.")
        if not (config.from_number or False):
            raise ValueError("Twilio backend needs a from_number.")
        self._config = config

    async def send(self, message: SmsMessage) -> None:
        from twilio.rest import Client

        sid = self._config.twilio_account_sid
        token = self._config.twilio_auth_token
        assert sid and token  # validated in __init__
        client = Client(sid, token.get_secret_value())

        from_number = message.from_number or self._config.from_number
        await asyncio.to_thread(
            lambda: client.messages.create(
                to=message.to,
                from_=from_number,
                body=message.body,
            )
        )

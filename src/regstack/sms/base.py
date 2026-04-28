from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

# E.164: leading '+', then 1-15 digits, no leading zero in the country code.
_E164 = re.compile(r"^\+[1-9]\d{1,14}$")


def is_valid_e164(phone: str) -> bool:
    """Whether ``phone`` is a valid E.164 international phone number.

    E.164 means a leading ``+``, then a country code starting with a
    non-zero digit, then up to 15 total digits. Used to validate
    user-supplied phone numbers before storing them or handing them to
    an SMS provider.

    Args:
        phone: A candidate phone number string (e.g. ``+15551234567``).

    Returns:
        ``True`` if ``phone`` matches the E.164 grammar.
    """
    return bool(_E164.fullmatch(phone))


@dataclass(frozen=True, slots=True)
class SmsMessage:
    """A rendered SMS ready to hand to an :class:`SmsService`."""

    to: str
    """Recipient phone number in E.164 format."""

    body: str
    """The SMS body. Implementations are not required to enforce the
    160-character GSM-7 limit; long messages may be split by the
    upstream provider."""

    from_number: str | None = None
    """Sender phone number in E.164. ``None`` lets the backend fall
    back to a configured default (e.g. ``config.sms.from_number``)."""


class SmsService(ABC):
    """Pluggable transport for sending an :class:`SmsMessage`.

    Bundled implementations:

    - :class:`~regstack.sms.null.NullSmsService` — discards messages,
      the default when SMS 2FA is off.
    - Amazon SNS (``aioboto3``) — needs the ``sns`` extra.
    - Twilio — needs the ``twilio`` extra.

    To plug in a different provider implement :meth:`send` and pass the
    instance to :meth:`RegStack.set_sms_backend
    <regstack.app.RegStack.set_sms_backend>`.
    """

    @abstractmethod
    async def send(self, message: SmsMessage) -> None:
        """Deliver one SMS. No retries; caller decides on failure.

        Args:
            message: The pre-rendered message.
        """
        ...

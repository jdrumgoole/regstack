from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

# E.164: leading '+', then 1-15 digits, no leading zero in the country code.
_E164 = re.compile(r"^\+[1-9]\d{1,14}$")


def is_valid_e164(phone: str) -> bool:
    return bool(_E164.fullmatch(phone))


@dataclass(frozen=True, slots=True)
class SmsMessage:
    to: str
    body: str
    from_number: str | None = None


class SmsService(ABC):
    @abstractmethod
    async def send(self, message: SmsMessage) -> None: ...

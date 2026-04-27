from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    html: str
    text: str
    from_address: str
    from_name: str

    @property
    def from_header(self) -> str:
        return f"{self.from_name} <{self.from_address}>"


class EmailService(ABC):
    @abstractmethod
    async def send(self, message: EmailMessage) -> None: ...

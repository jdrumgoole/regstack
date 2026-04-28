from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """A rendered, multipart email ready to hand to an :class:`EmailService`.

    Always carries both an ``html`` and a ``text`` body — the SMTP and
    SES backends emit a ``multipart/alternative`` message. The
    ``from_*`` fields are pre-resolved at composition time (from
    ``EmailConfig``), so a backend doesn't have to know the host's
    branding settings.
    """

    to: str
    """Recipient email address."""

    subject: str
    """The ``Subject:`` header."""

    html: str
    """The HTML body."""

    text: str
    """The plaintext body for clients that don't render HTML."""

    from_address: str
    """The bare ``user@host`` address."""

    from_name: str
    """The display name shown to the recipient."""

    @property
    def from_header(self) -> str:
        """The composed ``"Name <user@host>"`` ``From:`` header."""
        return f"{self.from_name} <{self.from_address}>"


class EmailService(ABC):
    """Pluggable transport for sending an :class:`EmailMessage`.

    Bundled implementations:

    - :class:`~regstack.email.console.ConsoleEmailService` — prints to
      stdout, used in dev and tests.
    - SMTP (``aiosmtplib``-backed) — for any SMTP relay.
    - Amazon SES (``aioboto3``) — needs the ``ses`` extra.

    To plug in a different provider (Postmark, SendGrid, MessageBird,
    …) implement :meth:`send` and pass the instance to
    :meth:`RegStack.set_email_backend
    <regstack.app.RegStack.set_email_backend>`.
    """

    @abstractmethod
    async def send(self, message: EmailMessage) -> None:
        """Deliver one email. Implementations should not retry — the
        caller decides whether a transient failure is fatal.

        Args:
            message: The pre-rendered message to deliver.

        Raises:
            Exception: Implementations may raise transport-specific
                errors. The caller (typically a router endpoint) is
                responsible for translating them into HTTP responses.
        """
        ...

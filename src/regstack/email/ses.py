from __future__ import annotations

from typing import TYPE_CHECKING

from regstack.email.base import EmailMessage, EmailService

if TYPE_CHECKING:
    from regstack.config.schema import EmailConfig


class SesEmailService(EmailService):
    """Sends mail via AWS SES. Requires the optional ``ses`` extra
    (``pip install regstack[ses]``) which pulls in ``aioboto3``.
    """

    def __init__(self, config: EmailConfig) -> None:
        try:
            import aioboto3  # noqa: F401  (import-time check)
        except ImportError as exc:
            raise RuntimeError(
                "The SES email backend requires the 'ses' extra. "
                "Install with `pip install regstack[ses]` or `uv sync --extra ses`."
            ) from exc
        self._config = config
        # Defer client construction to send() so each call gets a fresh
        # short-lived session. SES clients are cheap to instantiate.

    async def send(self, message: EmailMessage) -> None:
        import aioboto3

        session_kwargs = {}
        if self._config.ses_profile:
            session_kwargs["profile_name"] = self._config.ses_profile
        session = aioboto3.Session(**session_kwargs)

        async with session.client("ses", region_name=self._config.ses_region) as client:
            await client.send_email(
                Source=message.from_header,
                Destination={"ToAddresses": [message.to]},
                Message={
                    "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": message.text, "Charset": "UTF-8"},
                        "Html": {"Data": message.html, "Charset": "UTF-8"},
                    },
                },
            )

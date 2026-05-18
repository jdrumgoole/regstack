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
            # Bare `type: ignore` because aioboto3 lacks py.typed; with
            # the ses extra installed mypy emits import-untyped, without
            # it import-not-found. A compound code list trips
            # unused-ignore in whichever environment doesn't match.
            import aioboto3  # type: ignore  # noqa: F401  (import-time check)
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

        session_kwargs: dict[str, str] = {}
        if self._config.ses_profile:
            session_kwargs["profile_name"] = self._config.ses_profile
        if self._config.ses_access_key_id is not None:
            # ses_access_key_id and ses_secret_access_key are validated
            # to be set together by EmailConfig._validate_ses_creds. We
            # re-check at runtime (rather than `assert`) because Python
            # strips `assert` under `python -O` / `PYTHONOPTIMIZE=1`,
            # which is common in production containers — and a None
            # secret here would otherwise raise a cryptic AttributeError
            # on the .get_secret_value() call below.
            if self._config.ses_secret_access_key is None:
                raise RuntimeError(
                    "ses_access_key_id set without ses_secret_access_key — "
                    "EmailConfig validation should have caught this."
                )
            session_kwargs["aws_access_key_id"] = self._config.ses_access_key_id.get_secret_value()
            session_kwargs["aws_secret_access_key"] = (
                self._config.ses_secret_access_key.get_secret_value()
            )
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

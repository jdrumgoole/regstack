from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from typing import Any

import pytest

from regstack.config.schema import EmailConfig
from regstack.email.base import EmailMessage
from regstack.email.smtp import SmtpEmailService


def test_smtp_requires_host() -> None:
    with pytest.raises(ValueError, match="smtp_host is required"):
        SmtpEmailService(EmailConfig(backend="smtp"))


@pytest.mark.asyncio
async def test_smtp_send_invokes_aiosmtplib(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_send(message: MimeMessage, **kwargs: Any) -> None:
        captured["message"] = message
        captured["kwargs"] = kwargs

    monkeypatch.setattr("regstack.email.smtp.aiosmtplib.send", fake_send)

    service = SmtpEmailService(
        EmailConfig(
            backend="smtp",
            from_address="noreply@example.com",
            from_name="MyApp",
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_starttls=True,
            smtp_username="user",
        )
    )
    await service.send(
        EmailMessage(
            to="alice@example.com",
            subject="Hello",
            html="<p>hi</p>",
            text="hi",
            from_address="noreply@example.com",
            from_name="MyApp",
        )
    )

    assert captured["kwargs"]["hostname"] == "smtp.example.com"
    assert captured["kwargs"]["port"] == 587
    assert captured["kwargs"]["start_tls"] is True
    assert captured["kwargs"]["username"] == "user"
    assert captured["kwargs"]["password"] is None
    mime = captured["message"]
    assert mime["To"] == "alice@example.com"
    assert mime["Subject"] == "Hello"
    assert mime["From"] == "MyApp <noreply@example.com>"
    # Body should be multipart/alternative with text + html parts.
    payload_types = {part.get_content_type() for part in mime.walk()}
    assert "text/plain" in payload_types
    assert "text/html" in payload_types

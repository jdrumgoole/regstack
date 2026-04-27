from __future__ import annotations

from pathlib import Path

import pytest

from regstack.config.schema import EmailConfig
from regstack.email.composer import MailComposer


@pytest.fixture
def composer() -> MailComposer:
    return MailComposer(
        email_config=EmailConfig(
            backend="console", from_address="noreply@example.com", from_name="MyApp"
        ),
        app_name="MyApp",
    )


def test_verification_message_renders_subject_and_url(composer: MailComposer) -> None:
    message = composer.verification(
        to="alice@example.com",
        full_name="Alice",
        url="https://app.example.com/verify?token=abc",
    )
    assert message.to == "alice@example.com"
    assert "MyApp" in message.subject
    assert "https://app.example.com/verify?token=abc" in message.text
    assert "https://app.example.com/verify?token=abc" in message.html
    assert "Alice" in message.text
    assert message.from_header == "MyApp <noreply@example.com>"


def test_password_reset_message_includes_ttl(composer: MailComposer) -> None:
    message = composer.password_reset(
        to="bob@example.com",
        full_name=None,
        url="https://app.example.com/reset?token=xyz",
        ttl_minutes=30,
    )
    assert "30 minutes" in message.text
    assert "xyz" in message.html


def test_host_template_dir_overrides_defaults(tmp_path: Path) -> None:
    host_dir = tmp_path / "host_templates"
    host_dir.mkdir()
    (host_dir / "verification.subject.txt").write_text("Welcome to {{ app_name }}!\n")
    (host_dir / "verification.html").write_text("<p>Hi from host: {{ url }}</p>\n")
    (host_dir / "verification.txt").write_text("Hi from host: {{ url }}\n")

    composer = MailComposer(
        email_config=EmailConfig(
            backend="console", from_address="noreply@example.com", from_name="MyApp"
        ),
        app_name="MyApp",
        host_template_dirs=[host_dir],
    )
    message = composer.verification(
        to="x@example.com", full_name=None, url="https://x/verify?token=t"
    )
    assert message.subject == "Welcome to MyApp!"
    assert message.text.strip() == "Hi from host: https://x/verify?token=t"
    assert "<p>Hi from host:" in message.html


def test_add_template_dir_after_construction(tmp_path: Path) -> None:
    composer = MailComposer(
        email_config=EmailConfig(
            backend="console", from_address="noreply@example.com", from_name="MyApp"
        ),
        app_name="MyApp",
    )
    base_subject = composer.verification(
        to="x@example.com", full_name=None, url="https://x/verify?token=t"
    ).subject

    host_dir = tmp_path / "tpl"
    host_dir.mkdir()
    (host_dir / "verification.subject.txt").write_text("Custom subject\n")
    composer.add_template_dir(host_dir)

    new_subject = composer.verification(
        to="x@example.com", full_name=None, url="https://x/verify?token=t"
    ).subject
    assert new_subject == "Custom subject"
    assert new_subject != base_subject

"""Unit tests for capture regexes and the LogTailer subprocess plumbing."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from regstack.cli.validate.capture import (
    CONSOLE_BODY_MARKER_RE,
    LOGIN_MFA_CODE_RE,
    PHONE_SETUP_CODE_RE,
    VERIFICATION_URL_RE,
    extract_token_from_url,
)
from regstack.cli.validate.logtail import (
    LogTailer,
    LogTailError,
    TokenNotSeenError,
    parse_log_source,
)

# --- capture.py regex coverage ---------------------------------------------


def test_verification_url_re_matches_bundled_template_body() -> None:
    body = (
        "Hi Alice,\n"
        "\n"
        "Thanks for signing up to MyApp. Please confirm your email address by "
        "visiting the link below:\n"
        "\n"
        "  https://app.example.com/verify?token=abc123XYZ-_.\n"
    )
    m = VERIFICATION_URL_RE.search(body)
    assert m is not None
    assert m.group("url") == "https://app.example.com/verify?token=abc123XYZ-_."
    assert extract_token_from_url(m.group("url")) == "abc123XYZ-_."


def test_verification_url_re_matches_reset_path() -> None:
    body = "  https://example.com/account/reset-password?token=foo.bar.baz\n"
    m = VERIFICATION_URL_RE.search(body)
    assert m is not None
    assert m.group("url").endswith("token=foo.bar.baz")


def test_verification_url_re_matches_change_email_path() -> None:
    body = "  https://example.com/confirm-email-change?token=eyJ0eXAiOiJKV1Qi\n"
    m = VERIFICATION_URL_RE.search(body)
    assert m is not None
    assert "confirm-email-change" in m.group("url")


def test_login_mfa_code_re_matches_template() -> None:
    line = "[regstack/null-sms] To: +15551234567 | From: (unset) | Body: MyApp sign-in code: 482917. Expires in 5 minutes."
    m = LOGIN_MFA_CODE_RE.search(line)
    assert m is not None
    assert m.group("code") == "482917"


def test_phone_setup_code_re_matches_template() -> None:
    line = "MyApp verification code: 901234. It expires in 5 minutes."
    m = PHONE_SETUP_CODE_RE.search(line)
    assert m is not None
    assert m.group("code") == "901234"


def test_console_body_marker_re() -> None:
    assert CONSOLE_BODY_MARKER_RE.search(
        "INFO regstack.email.console:[regstack/console-email] text body:\nfoo"
    )
    assert not CONSOLE_BODY_MARKER_RE.search(
        "INFO regstack.email.console:[regstack/console-email] To: x"
    )


def test_extract_token_from_url_no_token_returns_none() -> None:
    assert extract_token_from_url("https://example.com/verify") is None


# --- parse_log_source ------------------------------------------------------


def test_parse_file_source() -> None:
    spec = parse_log_source("file:/var/log/regstack.log")
    assert spec.kind == "file"
    assert spec.argv[:1] == ["tail"]
    assert spec.argv[-1] == "/var/log/regstack.log"


def test_parse_ssh_source() -> None:
    spec = parse_log_source("ssh:deploy@host.example.com:/var/log/r.log")
    assert spec.kind == "ssh"
    assert "ssh" in spec.argv[0]
    assert "BatchMode=yes" in spec.argv
    assert "deploy@host.example.com" in spec.argv
    # Path is quoted into the remote shell command.
    assert any("/var/log/r.log" in a for a in spec.argv)


def test_parse_docker_source() -> None:
    spec = parse_log_source("docker:regstack-prod")
    assert spec.kind == "docker"
    assert spec.argv[:3] == ["docker", "logs", "-f"]
    assert spec.argv[-1] == "regstack-prod"


def test_parse_cmd_source() -> None:
    spec = parse_log_source("cmd:journalctl -fu regstack")
    assert spec.kind == "cmd"
    assert spec.argv == ["sh", "-c", "journalctl -fu regstack"]


def test_parse_bad_kind_rejected() -> None:
    with pytest.raises(ValueError, match="not one of"):
        parse_log_source("smb://share/log")


def test_parse_no_colon_rejected() -> None:
    with pytest.raises(ValueError, match="must be"):
        parse_log_source("file-with-no-colon")


def test_parse_ssh_without_remote_path_rejected() -> None:
    with pytest.raises(ValueError, match="user@host"):
        parse_log_source("ssh:deploy@host.example.com")


# --- LogTailer over a real subprocess (file: source) -----------------------


def _have_tail() -> bool:
    return shutil.which("tail") is not None


@pytest.mark.skipif(not _have_tail(), reason="tail(1) not on PATH")
@pytest.mark.asyncio
async def test_tailer_expect_url_against_file(tmp_path: Path) -> None:
    log_path = tmp_path / "r.log"
    log_path.write_text("")
    tailer = LogTailer(parse_log_source(f"file:{log_path}"))
    await tailer.start()
    try:
        # Append a line after start; tail -F will deliver it.
        async def _writer() -> None:
            await asyncio.sleep(0.1)
            with log_path.open("a") as f:
                f.write("INFO regstack.email.console:[regstack/console-email] text body:\n")
                f.write("Hi,\n\n  https://app.example.com/verify?token=secret123\n")
                f.flush()

        writer = asyncio.create_task(_writer())
        url = await tailer.expect_url(
            VERIFICATION_URL_RE,
            must_contain=["/verify"],
            timeout=5.0,
        )
        assert url == "https://app.example.com/verify?token=secret123"
        await writer
    finally:
        await tailer.close()


@pytest.mark.skipif(not _have_tail(), reason="tail(1) not on PATH")
@pytest.mark.asyncio
async def test_tailer_times_out_when_no_match(tmp_path: Path) -> None:
    log_path = tmp_path / "r.log"
    log_path.write_text("")
    tailer = LogTailer(parse_log_source(f"file:{log_path}"))
    await tailer.start()
    try:
        with pytest.raises(TokenNotSeenError):
            await tailer.expect_url(VERIFICATION_URL_RE, timeout=0.5)
    finally:
        await tailer.close()


@pytest.mark.asyncio
async def test_tailer_reports_unknown_command() -> None:
    spec = parse_log_source("cmd:this-binary-does-not-exist-x9y8z7")
    tailer = LogTailer(spec)
    # `sh -c` exists; the inner command will fail and the subprocess
    # will exit. The tailer surfaces that as LogTailError when something
    # is awaited.
    await tailer.start()
    try:
        with pytest.raises((LogTailError, TokenNotSeenError)):
            await tailer.expect_url(VERIFICATION_URL_RE, timeout=1.0)
    finally:
        await tailer.close()

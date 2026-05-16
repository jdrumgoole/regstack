"""Pin the OAuth callback log-sanitization helper.

The ``error`` query parameter on the OAuth callback is set by the
provider; a compromised or malicious provider could inject newlines or
ANSI escape sequences to forge or obscure log entries. ``_sanitize_for_log``
strips control characters and caps length before that value reaches the
log handler.

Flagged as I-3 in the 2026-05-15 / 2026-05-16 security reviews.
"""

from __future__ import annotations

from regstack.routers.oauth import _sanitize_for_log


def test_strips_newlines() -> None:
    assert _sanitize_for_log("line1\nline2") == "line1line2"
    assert _sanitize_for_log("line1\r\nline2") == "line1line2"


def test_strips_ansi_escape() -> None:
    # ESC (0x1b) is a control char; isprintable() filters it out.
    payload = "\x1b[31mred-text\x1b[0m"
    cleaned = _sanitize_for_log(payload)
    assert "\x1b" not in cleaned
    assert "red-text" in cleaned


def test_strips_bell_and_tab() -> None:
    assert _sanitize_for_log("a\x07b") == "ab"
    assert _sanitize_for_log("a\tb") == "ab"


def test_caps_length_at_200() -> None:
    long = "x" * 1000
    assert _sanitize_for_log(long) == "x" * 200


def test_preserves_normal_unicode() -> None:
    assert _sanitize_for_log("user denied — try again") == "user denied — try again"
    assert _sanitize_for_log("provider:google error=access_denied") == (
        "provider:google error=access_denied"
    )


def test_empty_string_safe() -> None:
    assert _sanitize_for_log("") == ""

from __future__ import annotations

import secrets

import pytest

from regstack.hooks import REDACTED, redact_token


def test_redacts_query_parameter() -> None:
    token = secrets.token_urlsafe(32)
    url = f"https://app.example.com/account/verify?token={token}"
    assert redact_token(url, token) == f"https://app.example.com/account/verify?token={REDACTED}"


def test_redacts_path_segment() -> None:
    token = secrets.token_urlsafe(32)
    url = f"https://app.example.com/verify/{token}"
    assert redact_token(url, token) == f"https://app.example.com/verify/{REDACTED}"


def test_redacts_hash_fragment() -> None:
    """`verify_url_template` lets a host put the token behind a hash route."""
    token = secrets.token_urlsafe(32)
    url = f"https://app.example.com/#/verify/{token}"
    assert token not in redact_token(url, token)


def test_redacts_jwt_shaped_token() -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln-bmF0dXJl"
    url = f"https://app.example.com/account/reset?token={token}"
    assert redact_token(url, token) == f"https://app.example.com/account/reset?token={REDACTED}"


def test_redacts_percent_encoded_occurrence() -> None:
    token = "abc/def+ghi"
    url = "https://app.example.com/verify?token=abc%2Fdef%2Bghi"
    assert redact_token(url, token) == f"https://app.example.com/verify?token={REDACTED}"


def test_redacts_every_occurrence() -> None:
    token = secrets.token_urlsafe(32)
    url = f"https://app.example.com/verify/{token}?token={token}"
    redacted = redact_token(url, token)
    assert token not in redacted
    assert redacted.count(REDACTED) == 2


@pytest.mark.parametrize("token", ["", None])
def test_empty_token_leaves_url_alone(token: str | None) -> None:
    url = "https://app.example.com/account/verify"
    assert redact_token(url, token or "") == url


def test_rest_of_url_survives() -> None:
    token = secrets.token_urlsafe(32)
    url = f"https://app.example.com/verify?token={token}&next=%2Fdashboard"
    redacted = redact_token(url, token)
    assert redacted.endswith("&next=%2Fdashboard")
    assert redacted.startswith("https://app.example.com/verify?token=")

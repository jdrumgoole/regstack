"""Tests for ``RegStackConfig.resolve_email_link_prefix``.

The verify / reset-password / confirm-email-change emails carry a URL
composed as ``f"{base_url}{prefix}/<page>?token=..."``. The prefix is
resolved from three knobs in a specific order; pin the contract here.
"""

from __future__ import annotations

from pydantic import SecretStr

from regstack.config.schema import RegStackConfig


def _cfg(**overrides) -> RegStackConfig:
    base = {
        "jwt_secret": SecretStr("x" * 32),
        "database_url": SecretStr("sqlite+aiosqlite:///:memory:"),
    }
    base.update(overrides)
    return RegStackConfig(**base)


def test_explicit_prefix_wins() -> None:
    cfg = _cfg(
        email_link_prefix="/custom-path",
        enable_ui_router=True,
        ui_prefix="/account",
    )
    assert cfg.resolve_email_link_prefix() == "/custom-path"


def test_explicit_empty_string_wins_over_ui_router_default() -> None:
    """Operators can opt out of the auto-derived ui_prefix by setting
    ``email_link_prefix = ""`` explicitly. Distinguishable from leaving it
    ``None`` because the latter falls back to ui_prefix when the UI is on.
    """
    cfg = _cfg(
        email_link_prefix="",
        enable_ui_router=True,
        ui_prefix="/account",
    )
    assert cfg.resolve_email_link_prefix() == ""


def test_auto_resolves_to_ui_prefix_when_ui_router_enabled() -> None:
    cfg = _cfg(
        email_link_prefix=None,
        enable_ui_router=True,
        ui_prefix="/account",
    )
    assert cfg.resolve_email_link_prefix() == "/account"


def test_auto_resolves_to_empty_when_ui_router_disabled() -> None:
    """SPA / headless deploys: ui_prefix is irrelevant because nothing
    serves under it. Default to the historical bare-path behaviour so
    a SPA at ``/`` can intercept the link itself.
    """
    cfg = _cfg(
        email_link_prefix=None,
        enable_ui_router=False,
        ui_prefix="/account",
    )
    assert cfg.resolve_email_link_prefix() == ""


def test_trailing_slash_stripped() -> None:
    cfg = _cfg(email_link_prefix="/foo/")
    assert cfg.resolve_email_link_prefix() == "/foo"


def test_ui_prefix_trailing_slash_stripped_when_auto_resolved() -> None:
    cfg = _cfg(
        email_link_prefix=None,
        enable_ui_router=True,
        ui_prefix="/account/",
    )
    assert cfg.resolve_email_link_prefix() == "/account"

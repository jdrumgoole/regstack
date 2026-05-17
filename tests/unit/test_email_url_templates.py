"""Tests for ``RegStackConfig.resolve_{verify,password_reset,
email_change}_url``.

When ``*_url_template`` is None (default), the resolvers fall back to
the historical prefix-based composition that ``resolve_email_link_prefix``
already pins. When a template is set, the resolver replaces ``{base_url}``
and ``{token}`` literally and ignores the prefix entirely — that's the
escape hatch for SPAs whose router shape doesn't fit the canonical
``/verify?token=...`` form.
"""

from __future__ import annotations

from pydantic import SecretStr

from regstack.config.schema import RegStackConfig


def _cfg(**overrides) -> RegStackConfig:
    base = {
        "jwt_secret": SecretStr("x" * 32),
        "database_url": SecretStr("sqlite+aiosqlite:///:memory:"),
        "base_url": "https://app.example.com",
    }
    base.update(overrides)
    return RegStackConfig(**base)


def test_verify_url_defaults_to_legacy_composition() -> None:
    cfg = _cfg()
    assert cfg.resolve_verify_url("TOK") == "https://app.example.com/verify?token=TOK"


def test_password_reset_url_defaults_to_legacy_composition() -> None:
    cfg = _cfg()
    assert (
        cfg.resolve_password_reset_url("TOK")
        == "https://app.example.com/reset-password?token=TOK"
    )


def test_email_change_url_defaults_to_legacy_composition() -> None:
    cfg = _cfg()
    assert (
        cfg.resolve_email_change_url("TOK")
        == "https://app.example.com/confirm-email-change?token=TOK"
    )


def test_default_composition_honours_email_link_prefix() -> None:
    cfg = _cfg(email_link_prefix="/my-app")
    assert (
        cfg.resolve_verify_url("TOK")
        == "https://app.example.com/my-app/verify?token=TOK"
    )


def test_verify_url_template_overrides_completely() -> None:
    """A SPA on hash routing needs ``/#/verify/TOK`` rather than
    ``/verify?token=TOK``. The template fully replaces the composition
    — no question mark, no `?token=` query string, no prefix concat.
    """
    cfg = _cfg(verify_url_template="{base_url}/#/verify/{token}")
    assert cfg.resolve_verify_url("TOK") == "https://app.example.com/#/verify/TOK"


def test_url_template_can_target_a_different_host() -> None:
    """``{base_url}`` is just a substitution — the template can ignore
    it entirely and route to a different subdomain or app.
    """
    cfg = _cfg(verify_url_template="https://auth.example.com/v?t={token}")
    assert cfg.resolve_verify_url("TOK") == "https://auth.example.com/v?t=TOK"


def test_each_template_is_independent() -> None:
    cfg = _cfg(
        verify_url_template="https://v.example.com/{token}",
        password_reset_url_template=None,
        email_change_url_template="https://ec.example.com/{token}",
        email_link_prefix="/legacy",
    )
    assert cfg.resolve_verify_url("A") == "https://v.example.com/A"
    # password_reset_url_template is None → falls back to prefix-based
    assert (
        cfg.resolve_password_reset_url("B")
        == "https://app.example.com/legacy/reset-password?token=B"
    )
    assert cfg.resolve_email_change_url("C") == "https://ec.example.com/C"


def test_token_is_not_url_encoded_by_resolver() -> None:
    """Substitution is literal. Hosts who want their tokens URL-encoded
    do it themselves (regstack tokens are base64url-safe already, but a
    custom template might land them in a path segment where encoding
    rules are caller-defined).
    """
    cfg = _cfg(verify_url_template="{base_url}/verify/{token}")
    assert cfg.resolve_verify_url("a+b/c=") == "https://app.example.com/verify/a+b/c="


def test_base_url_trailing_slash_trimmed_for_templates_too() -> None:
    cfg = _cfg(
        base_url="https://app.example.com/",
        verify_url_template="{base_url}/v/{token}",
    )
    assert cfg.resolve_verify_url("T") == "https://app.example.com/v/T"

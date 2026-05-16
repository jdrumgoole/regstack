"""Pin the rate-limit map's coverage and the config-field bridge.

The per-IP rate-limit feature works by mapping a config field name
(``login_rate_limit``, etc.) to the router path it throttles. Every
state-mutating or authentication-adjacent endpoint should appear in
this map — otherwise hosts have no way to configure a per-IP throttle
for it.

Flagged as I-2 in the 2026-05-15 / 2026-05-16 security reviews:
``/login/mfa-confirm`` and ``/oauth/exchange`` were absent from the
map, leaving hosts unable to throttle distributed brute-force attempts
on those endpoints.
"""

from __future__ import annotations

import secrets

from regstack.auth.rate_limit import ROUTE_LIMIT_MAP, collect_route_limits
from regstack.config.schema import RegStackConfig

_REQUIRED_ENTRIES = {
    "login_rate_limit": "/login",
    "login_mfa_confirm_rate_limit": "/login/mfa-confirm",
    "register_rate_limit": "/register",
    "forgot_password_rate_limit": "/forgot-password",
    "reset_password_rate_limit": "/reset-password",
    "verify_rate_limit": "/verify",
    "resend_verification_rate_limit": "/resend-verification",
    "change_password_rate_limit": "/change-password",
    "change_email_rate_limit": "/change-email",
    "confirm_email_change_rate_limit": "/confirm-email-change",
    "delete_account_rate_limit": "/account",
    "oauth_exchange_rate_limit": "/oauth/exchange",
}


def test_route_limit_map_covers_every_required_endpoint() -> None:
    """A missing entry means hosts can't configure a per-IP throttle for
    that endpoint — surface it as a test failure, not a runtime hole."""
    missing = {k: v for k, v in _REQUIRED_ENTRIES.items() if ROUTE_LIMIT_MAP.get(k) != v}
    assert not missing, f"ROUTE_LIMIT_MAP dropped or changed required entries: {missing}"


def test_every_map_entry_has_a_matching_config_field() -> None:
    """A field in the map without the corresponding `<name>: str | None` on
    ``RegStackConfig`` means ``collect_route_limits`` will silently skip it.
    """
    cfg = RegStackConfig(jwt_secret=secrets.token_urlsafe(32))
    declared = set(type(cfg).model_fields)
    missing_on_config = [name for name in ROUTE_LIMIT_MAP if name not in declared]
    assert not missing_on_config, (
        f"ROUTE_LIMIT_MAP keys with no matching RegStackConfig field: {missing_on_config}"
    )


def test_collect_route_limits_picks_up_new_fields() -> None:
    """Configure the two newly-covered endpoints and assert they appear
    in the collected limits."""
    cfg = RegStackConfig(
        jwt_secret=secrets.token_urlsafe(32),
        login_mfa_confirm_rate_limit="3/minute",
        oauth_exchange_rate_limit="5/minute",
    )
    limits = collect_route_limits(cfg)
    assert limits.get("/login/mfa-confirm") == "3/minute"
    assert limits.get("/oauth/exchange") == "5/minute"

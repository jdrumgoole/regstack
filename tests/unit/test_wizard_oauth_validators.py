"""Per-step validator tests.

Table-driven across every step of the OAuth setup wizard. Pure
Python — no FastAPI involved, fast (<100 ms total).
"""

from __future__ import annotations

import pytest

from regstack.wizard.oauth_google.validators import (
    NUM_STEPS,
    FieldError,
    ValidateResult,
    validate_all,
    validate_step,
)


def _ok() -> ValidateResult:
    return ValidateResult(ok=True)


# ---------------------------------------------------------------------------
# Out-of-range step indices
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_step", [-1, NUM_STEPS, NUM_STEPS + 5, "0", None])
def test_unknown_step_returns_form_error(bad_step) -> None:
    result = validate_step(bad_step, {})
    assert result.ok is False
    assert any(e.field == "_form" for e in result.errors)


# ---------------------------------------------------------------------------
# Step 0 — welcome
# ---------------------------------------------------------------------------


def test_step_0_welcome_passes_with_no_input() -> None:
    assert validate_step(0, {}).ok is True


# ---------------------------------------------------------------------------
# Step 1 — detect existing
# ---------------------------------------------------------------------------


def test_step_1_no_existing_oauth_passes() -> None:
    assert validate_step(1, {"existing_oauth": False}).ok is True


def test_step_1_existing_oauth_requires_confirmation() -> None:
    result = validate_step(1, {"existing_oauth": True, "replace_existing": False})
    assert result.ok is False
    assert result.errors == [
        FieldError(
            "replace_existing", "Confirm you want to replace the existing OAuth configuration."
        )
    ]


def test_step_1_existing_oauth_with_confirmation_passes() -> None:
    assert validate_step(1, {"existing_oauth": True, "replace_existing": True}).ok is True


# ---------------------------------------------------------------------------
# Step 2 — base URL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "https://example.com",
        "https://app.example.com:443",
        "http://10.0.0.5",
    ],
)
def test_step_2_accepts_valid_urls(url: str) -> None:
    assert validate_step(2, {"base_url": url}).ok is True


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "example.com",  # missing scheme
        "ftp://example.com",  # wrong scheme
        "http://",  # missing host
        "https:///foo",  # missing host
    ],
)
def test_step_2_rejects_invalid_urls(url: str) -> None:
    result = validate_step(2, {"base_url": url})
    assert result.ok is False
    assert any(e.field == "base_url" for e in result.errors)


def test_step_2_missing_field_rejects() -> None:
    result = validate_step(2, {})
    assert result.ok is False
    assert any(e.field == "base_url" for e in result.errors)


# ---------------------------------------------------------------------------
# Steps 3-6: self-attested wait points
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step", [3, 4, 5, 6])
def test_self_attested_steps_always_pass(step: int) -> None:
    assert validate_step(step, {}).ok is True


# ---------------------------------------------------------------------------
# Step 7 — credentials
# ---------------------------------------------------------------------------


def _good_creds() -> dict:
    return {
        "client_id": "12345-abcde.apps.googleusercontent.com",
        "client_secret": "GOCSPX-1234567890abcdef",
    }


def test_step_7_happy_path() -> None:
    assert validate_step(7, _good_creds()).ok is True


def test_step_7_missing_client_id() -> None:
    result = validate_step(7, {**_good_creds(), "client_id": ""})
    assert result.ok is False
    assert any(e.field == "client_id" for e in result.errors)


@pytest.mark.parametrize(
    "client_id",
    [
        "not-a-google-id",
        "abc.googleusercontent.com",  # missing .apps.
        "abc.apps.example.com",  # wrong host
        "abc.apps.googleusercontent.co.uk",  # wrong tld
    ],
)
def test_step_7_rejects_malformed_client_id(client_id: str) -> None:
    result = validate_step(7, {**_good_creds(), "client_id": client_id})
    assert result.ok is False
    assert any(e.field == "client_id" and "googleusercontent" in e.message for e in result.errors)


def test_step_7_missing_client_secret() -> None:
    result = validate_step(7, {**_good_creds(), "client_secret": ""})
    assert result.ok is False
    assert any(e.field == "client_secret" for e in result.errors)


def test_step_7_too_short_client_secret() -> None:
    result = validate_step(7, {**_good_creds(), "client_secret": "short"})
    assert result.ok is False
    assert any(e.field == "client_secret" for e in result.errors)


def test_step_7_too_long_client_secret() -> None:
    result = validate_step(7, {**_good_creds(), "client_secret": "x" * 200})
    assert result.ok is False
    assert any(e.field == "client_secret" for e in result.errors)


def test_step_7_collects_multiple_errors() -> None:
    result = validate_step(7, {"client_id": "bad", "client_secret": ""})
    assert result.ok is False
    fields = {e.field for e in result.errors}
    assert fields == {"client_id", "client_secret"}


# ---------------------------------------------------------------------------
# Steps 8-9: boolean inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [True, False])
def test_step_8_accepts_booleans(value: bool) -> None:
    assert validate_step(8, {"auto_link_verified_emails": value}).ok is True


def test_step_8_rejects_non_boolean() -> None:
    result = validate_step(8, {"auto_link_verified_emails": "yes"})
    assert result.ok is False
    assert any(e.field == "auto_link_verified_emails" for e in result.errors)


def test_step_8_default_is_ok() -> None:
    """Missing field defaults to False, which is valid."""
    assert validate_step(8, {}).ok is True


@pytest.mark.parametrize("value", [True, False])
def test_step_9_accepts_booleans(value: bool) -> None:
    assert validate_step(9, {"enforce_mfa_on_oauth_signin": value}).ok is True


# ---------------------------------------------------------------------------
# Steps 10 and 11: review / write
# ---------------------------------------------------------------------------


def _full_payload() -> dict:
    return {
        "existing_oauth": False,
        "replace_existing": False,
        "base_url": "https://app.example.com",
        **_good_creds(),
        "auto_link_verified_emails": False,
        "enforce_mfa_on_oauth_signin": False,
    }


def test_step_10_happy_path() -> None:
    assert validate_step(10, _full_payload()).ok is True


def test_step_10_jumps_to_failing_step() -> None:
    bad = {**_full_payload(), "client_id": "bad"}
    result = validate_step(10, bad)
    assert result.ok is False
    assert result.jump_to == 7  # credentials step


def test_step_10_jumps_to_base_url_when_that_fails() -> None:
    bad = {**_full_payload(), "base_url": ""}
    result = validate_step(10, bad)
    assert result.ok is False
    assert result.jump_to == 2


def test_step_11_replays_full_validation() -> None:
    bad = {**_full_payload(), "client_secret": ""}
    result = validate_step(11, bad)
    assert result.ok is False
    assert result.jump_to == 7


def test_validate_all_returns_first_failure_only() -> None:
    """If multiple steps fail, validate_all returns the earliest."""
    bad = {
        **_full_payload(),
        "base_url": "",  # step 2 fails
        "client_id": "bad",  # step 7 also would fail
    }
    result = validate_all(bad)
    assert result.ok is False
    assert result.jump_to == 2  # earliest failure

"""Unit tests for the SES wizard's per-step validators."""

from __future__ import annotations

from regstack.wizard.ses.validators import (
    NUM_STEPS,
    validate_all,
    validate_step,
)


def test_step_count() -> None:
    assert NUM_STEPS == 9


def test_step_welcome_passes() -> None:
    assert validate_step(0, {}).ok


def test_step_detect_existing_requires_confirmation() -> None:
    result = validate_step(1, {"existing_ses": True, "replace_existing": False})
    assert not result.ok
    assert result.errors[0].field == "replace_existing"


def test_step_detect_existing_no_existing_passes() -> None:
    assert validate_step(1, {"existing_ses": False}).ok


def test_step_detect_existing_with_confirmation_passes() -> None:
    assert validate_step(1, {"existing_ses": True, "replace_existing": True}).ok


def test_step_region_requires_value() -> None:
    assert not validate_step(2, {}).ok
    assert not validate_step(2, {"ses_region": ""}).ok


def test_step_region_rejects_unknown() -> None:
    result = validate_step(2, {"ses_region": "atlantis-1"})
    assert not result.ok
    assert "not a known SES region" in result.errors[0].message


def test_step_region_accepts_known() -> None:
    for region in ("us-east-1", "eu-west-1", "ap-south-1"):
        assert validate_step(2, {"ses_region": region}).ok


def test_step_credentials_profile_mode_requires_name() -> None:
    assert not validate_step(3, {"credential_source": "profile"}).ok
    assert validate_step(3, {"credential_source": "profile", "ses_profile": "prod"}).ok


def test_step_credentials_explicit_mode_requires_both_keys() -> None:
    assert not validate_step(3, {"credential_source": "explicit"}).ok
    assert not validate_step(
        3, {"credential_source": "explicit", "ses_access_key_id": "AKIA..."}
    ).ok
    assert validate_step(
        3,
        {
            "credential_source": "explicit",
            "ses_access_key_id": "AKIA...",
            "ses_secret_access_key": "secret",
        },
    ).ok


def test_step_credentials_chain_mode_passes_with_no_fields() -> None:
    assert validate_step(3, {"credential_source": "chain"}).ok


def test_step_credentials_unknown_source_rejected() -> None:
    assert not validate_step(3, {"credential_source": "magic"}).ok


def test_step_sender_email_shape() -> None:
    assert not validate_step(4, {}).ok
    assert not validate_step(4, {"from_address": "not-an-email"}).ok
    assert validate_step(4, {"from_address": "noreply@example.com"}).ok


def test_step_sandbox_attestation_required_when_in_sandbox() -> None:
    assert not validate_step(5, {"aws_in_sandbox": True}).ok
    assert validate_step(5, {"aws_in_sandbox": True, "sandbox_attested": True}).ok


def test_step_sandbox_no_attestation_needed_when_not_sandbox() -> None:
    assert validate_step(5, {"aws_in_sandbox": False}).ok
    assert validate_step(5, {}).ok


def test_step_test_send_recipient_shape() -> None:
    assert not validate_step(6, {}).ok
    assert validate_step(6, {"skip_test_send": True}).ok
    assert validate_step(6, {"test_recipient": "probe@example.com"}).ok
    # falls back to from_address
    assert validate_step(6, {"from_address": "noreply@example.com"}).ok


def test_step_test_send_invalid_recipient() -> None:
    assert not validate_step(6, {"test_recipient": "not-an-email"}).ok


def test_unknown_step_index_returns_error_not_exception() -> None:
    assert not validate_step(-1, {}).ok
    assert not validate_step(99, {}).ok


def test_validate_all_chains_every_step() -> None:
    good_inputs = {
        "existing_ses": False,
        "ses_region": "eu-west-1",
        "credential_source": "chain",
        "from_address": "noreply@example.com",
        "sandbox_attested": True,
        "skip_test_send": True,
    }
    assert validate_all(good_inputs).ok


def test_validate_all_jumps_to_first_failing_step() -> None:
    inputs = {
        "ses_region": "eu-west-1",
        "credential_source": "profile",
        # missing ses_profile
        "from_address": "noreply@example.com",
    }
    result = validate_all(inputs)
    assert not result.ok
    assert result.jump_to == 3
    assert any(e.field == "ses_profile" for e in result.errors)

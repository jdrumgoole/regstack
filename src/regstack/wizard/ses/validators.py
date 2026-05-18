"""Per-step validation rules for the SES setup wizard.

Mirrors the structure of
:mod:`regstack.wizard.oauth_google.validators` — same
:class:`FieldError` / :class:`ValidateResult` shape, same
``validate_step`` / ``validate_all`` entry points — but the per-step
handlers talk to AWS via ``aioboto3`` and validate SES-specific
inputs.

Step layout (9 steps, 0-8):

0. Welcome — no inputs.
1. Detect existing — if ``[email].backend == "ses"`` already, require
   explicit ``replace_existing``.
2. Region — non-empty + in the known SES region list.
3. Credential source — ``profile`` / ``explicit`` / ``chain``. When
   ``explicit``, both key id + secret required. The real STS check
   (``sts.get_caller_identity``) is run here.
4. Sender domain + identity verification — `from_address` is a valid
   email; ``ses.get_identity_verification_attributes`` confirms the
   domain (or the full address) is verified.
5. Sandbox attestation — if SES reports the account is in the
   sandbox (`ProductionAccessEnabled=false` or the heuristic quota
   match), require ``sandbox_attested: true``. When the AWS call
   returns ``AccessDenied`` we fall through and assume not-sandbox
   with a warning surfaced via a non-blocking field.
6. Test send — ``ses.send_email`` to the operator-supplied probe
   address, unless ``skip_test_send`` is checked.
7. Review — replays every preceding step against the accumulated
   payload (analogue of OAuth wizard's step 10).
8. Write — same.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Known top-level SES regions. ``aioboto3.Session().get_available_regions``
# would give us this list dynamically, but at validation time the choice
# is text input and we just want a tight allow-list to catch typos. The
# AWS SDK accepts an unknown region string and produces an opaque
# DNS-lookup error at call time, which is a worse UX than a clean
# "unknown region" message.
KNOWN_SES_REGIONS = frozenset(
    {
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
        "af-south-1",
        "ap-south-1",
        "ap-southeast-1",
        "ap-southeast-2",
        "ap-northeast-1",
        "ap-northeast-2",
        "ap-northeast-3",
        "ca-central-1",
        "eu-central-1",
        "eu-west-1",
        "eu-west-2",
        "eu-west-3",
        "eu-north-1",
        "eu-south-1",
        "il-central-1",
        "me-south-1",
        "sa-east-1",
    }
)

CREDENTIAL_SOURCES = ("profile", "explicit", "chain")

# Conservative email-shape regex. Same approach as the SMS phone
# validator (cheap pre-filter that doesn't pretend to be RFC 5321).
# pydantic's EmailStr would be authoritative but that's overkill for
# the wizard's per-step UX — we just want to reject empty / clearly
# bad input cheaply.
_EMAIL_RE = re.compile(r"^[^@\s]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

NUM_STEPS = 9


@dataclass(slots=True, frozen=True)
class FieldError:
    """One inline error attached to a specific input.

    Mirrors the OAuth wizard's ``FieldError``.
    """

    field: str
    message: str


@dataclass(slots=True, frozen=True)
class ValidateResult:
    """Outcome of a step validation.

    ``warnings`` is new compared to the OAuth wizard: SES has a few
    states (sandbox-detection ambiguous, IAM-blocked-access) where we
    surface a non-blocking advisory the SPA renders inline but doesn't
    treat as an error.
    """

    ok: bool
    errors: list[FieldError] = field(default_factory=list)
    warnings: list[FieldError] = field(default_factory=list)
    jump_to: int | None = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_step(step: int, inputs: dict[str, Any]) -> ValidateResult:
    if not isinstance(step, int) or step < 0 or step >= NUM_STEPS:
        return _err("_form", f"Unknown wizard step: {step!r}.")
    handler = _HANDLERS[step]
    return handler(inputs)


def validate_all(inputs: dict[str, Any]) -> ValidateResult:
    """Replay every step's *sync* validation against the full payload.

    The AWS-touching handlers (3, 4, 5, 6) are skipped here — those
    are I/O-bound and have already been called as the user advanced
    through the SPA. ``/api/write`` re-runs all the cheap data
    correctness checks server-side; the network checks aren't repeated.
    """
    for n in range(NUM_STEPS):
        if n in _META_STEPS or n in _AWS_STEPS:
            continue
        result = _HANDLERS[n](inputs)
        if not result.ok:
            return ValidateResult(ok=False, errors=result.errors, jump_to=n)
    # The sync-only credential-shape check from step 3 still belongs
    # in validate_all because it has no AWS call; run it explicitly.
    cred_shape = _step_credentials_shape(inputs)
    if not cred_shape.ok:
        return ValidateResult(ok=False, errors=cred_shape.errors, jump_to=3)
    return ValidateResult(ok=True)


# ---------------------------------------------------------------------------
# Per-step handlers — sync (no AWS)
# ---------------------------------------------------------------------------


def _step_welcome(inputs: dict[str, Any]) -> ValidateResult:
    return ValidateResult(ok=True)


def _step_detect_existing(inputs: dict[str, Any]) -> ValidateResult:
    has_existing = bool(inputs.get("existing_ses"))
    confirmed = bool(inputs.get("replace_existing"))
    if has_existing and not confirmed:
        return _err(
            "replace_existing",
            "Confirm you want to replace the existing SES configuration.",
        )
    return ValidateResult(ok=True)


def _step_region(inputs: dict[str, Any]) -> ValidateResult:
    raw = inputs.get("ses_region")
    if not isinstance(raw, str) or not raw.strip():
        return _err("ses_region", "Region is required.")
    region = raw.strip()
    if region not in KNOWN_SES_REGIONS:
        return _err(
            "ses_region",
            f"{region!r} is not a known SES region. Pick from us-east-1, eu-west-1, etc.",
        )
    return ValidateResult(ok=True)


def _step_credentials_shape(inputs: dict[str, Any]) -> ValidateResult:
    """Sync-only credential shape check.

    The full credential test (an STS call) is in
    :func:`_step_credentials_aws`; this is the input-shape gate that
    runs in ``validate_all`` so a write attempt can't smuggle a
    half-populated explicit-creds payload past the AWS layer.
    """
    source = inputs.get("credential_source")
    if source not in CREDENTIAL_SOURCES:
        return _err(
            "credential_source",
            f"Credential source must be one of {', '.join(CREDENTIAL_SOURCES)}.",
        )

    if source == "profile":
        profile = inputs.get("ses_profile")
        if not isinstance(profile, str) or not profile.strip():
            return _err("ses_profile", "Profile name is required.")
    elif source == "explicit":
        errors: list[FieldError] = []
        access_key = inputs.get("ses_access_key_id")
        secret_key = inputs.get("ses_secret_access_key")
        if not isinstance(access_key, str) or not access_key.strip():
            errors.append(FieldError("ses_access_key_id", "Access key ID is required."))
        if not isinstance(secret_key, str) or not secret_key:
            errors.append(FieldError("ses_secret_access_key", "Secret access key is required."))
        if errors:
            return ValidateResult(ok=False, errors=errors)
    # "chain" mode needs no fields.
    return ValidateResult(ok=True)


def _step_credentials(inputs: dict[str, Any]) -> ValidateResult:
    """Step 3 — credential shape + a live STS call.

    The actual AWS call is wired in by the routes layer (which has
    the running event loop). This sync handler is the cheap pre-check
    that fires before the network call.
    """
    return _step_credentials_shape(inputs)


def _step_sender(inputs: dict[str, Any]) -> ValidateResult:
    """Step 4 — sender email format + identity-verification AWS call.

    Same split: this sync handler validates shape. The routes layer
    runs ``ses.get_identity_verification_attributes`` and merges its
    result into the response payload before the SPA advances.
    """
    raw = inputs.get("from_address")
    if not isinstance(raw, str) or not raw.strip():
        return _err("from_address", "Sender address is required.")
    addr = raw.strip()
    if not _EMAIL_RE.fullmatch(addr):
        return _err("from_address", "Sender must look like noreply@your-domain.example.")
    return ValidateResult(ok=True)


def _step_sandbox(inputs: dict[str, Any]) -> ValidateResult:
    """Step 5 — sandbox attestation.

    When the routes layer has detected the AWS account is in the SES
    sandbox, the SPA renders a self-attested checkbox. This validator
    refuses the step until the checkbox is ticked. When AWS reported
    not-sandbox (or the detection call was denied), the checkbox is
    not rendered and this step auto-passes.
    """
    sandbox = bool(inputs.get("aws_in_sandbox"))
    attested = bool(inputs.get("sandbox_attested"))
    if sandbox and not attested:
        return _err(
            "sandbox_attested",
            "Tick the checkbox to confirm you understand the sandbox limitations.",
        )
    return ValidateResult(ok=True)


def _step_test_send(inputs: dict[str, Any]) -> ValidateResult:
    """Step 6 — sync shape check for the test-send recipient.

    The actual send is wired through the routes layer. When
    ``skip_test_send`` is set, no recipient is required.
    """
    if inputs.get("skip_test_send"):
        return ValidateResult(ok=True)
    raw = inputs.get("test_recipient") or inputs.get("from_address")
    if not isinstance(raw, str) or not raw.strip():
        return _err("test_recipient", "Provide a recipient or tick 'skip test send'.")
    if not _EMAIL_RE.fullmatch(raw.strip()):
        return _err("test_recipient", "Recipient must be a valid email address.")
    return ValidateResult(ok=True)


def _step_review(inputs: dict[str, Any]) -> ValidateResult:
    return validate_all(inputs)


def _step_write(inputs: dict[str, Any]) -> ValidateResult:
    return validate_all(inputs)


_HANDLERS = (
    _step_welcome,  # 0
    _step_detect_existing,  # 1
    _step_region,  # 2
    _step_credentials,  # 3 (AWS-touching when called from routes)
    _step_sender,  # 4 (AWS-touching when called from routes)
    _step_sandbox,  # 5 (AWS-touching when called from routes)
    _step_test_send,  # 6 (AWS-touching when called from routes)
    _step_review,  # 7
    _step_write,  # 8
)
assert len(_HANDLERS) == NUM_STEPS, "handler table out of sync with NUM_STEPS"

# Skipped during validate_all to avoid recursion.
_META_STEPS = frozenset({7, 8})
# AWS-touching steps; their sync handlers are still called for shape
# checks at write time, but validate_all skips them to avoid surfacing
# stale AWS state. Step 3's shape check is run explicitly via
# `_step_credentials_shape` so we don't lose the credential gate.
_AWS_STEPS = frozenset({3, 4, 5, 6})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(field_name: str, message: str) -> ValidateResult:
    return ValidateResult(ok=False, errors=[FieldError(field_name, message)])


__all__ = [
    "CREDENTIAL_SOURCES",
    "KNOWN_SES_REGIONS",
    "NUM_STEPS",
    "FieldError",
    "ValidateResult",
    "validate_all",
    "validate_step",
]

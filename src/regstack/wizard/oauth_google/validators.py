"""Per-step validation rules for the Google OAuth setup wizard.

Every "Next" button click in the SPA hits
``POST /api/step/{n}/validate`` with the inputs collected so far. The
endpoint dispatches into :func:`validate_step` here. The same logic
runs again inside ``/api/write`` against the full payload so a user
who URL-hacks past the gate still can't write a malformed config.

The wizard is 12 steps (0-11). See ``tasks/oauth-design.md`` and the
M4 wizard plan for the per-step UX.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

# Google's documented client_id shape: opaque string, ends with
# ``.apps.googleusercontent.com``. We're permissive on the prefix.
_GOOGLE_CLIENT_ID = re.compile(r"^[\w.\-]+\.apps\.googleusercontent\.com$")

CLIENT_SECRET_MIN = 8
CLIENT_SECRET_MAX = 128

# Total step count for the wizard. Used by the routes layer to bound
# the path parameter on /api/step/{n}/validate.
NUM_STEPS = 12


@dataclass(slots=True, frozen=True)
class FieldError:
    """One inline error attached to a specific input.

    Attributes:
        field: The form field name the error attaches to. Use
            ``"_form"`` for whole-form errors that aren't tied to a
            single field.
        message: Human-readable, ready to render. Don't include the
            field name — the SPA renders it next to the field.
    """

    field: str
    message: str


@dataclass(slots=True, frozen=True)
class ValidateResult:
    """Outcome of a step validation.

    Attributes:
        ok: True when the step passes and the SPA may advance.
        errors: One entry per failing input. Empty when ``ok``.
        jump_to: Optional step index the SPA should jump to instead
            of advancing. Set by step 10 (Review) when an earlier
            step's data is invalid — the SPA jumps the user back to
            that step rather than letting them write a bad config.
    """

    ok: bool
    errors: list[FieldError] = field(default_factory=list)
    jump_to: int | None = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_step(step: int, inputs: dict[str, Any]) -> ValidateResult:
    """Validate the inputs for one wizard step.

    Args:
        step: Step index (0-11). Out-of-range values return an error
            result rather than raising. The SPA shouldn't be hitting
            unknown steps, but if it does we want a clean refusal.
        inputs: The accumulated wizard state, as the SPA would post
            it. The function picks the fields it needs by name.

    Returns:
        :class:`ValidateResult` — never raises on input shape.
    """
    if not isinstance(step, int) or step < 0 or step >= NUM_STEPS:
        return _err("_form", f"Unknown wizard step: {step!r}.")
    handler = _HANDLERS[step]
    return handler(inputs)


def validate_all(inputs: dict[str, Any]) -> ValidateResult:
    """Run every step's validation against the full payload.

    Used by ``/api/write`` (and step 10's Review gate) to refuse any
    request that has accumulated bad data through URL hacking or
    direct API access. Returns the **first** failing step's result,
    with ``jump_to`` set to that step.

    Skips the review (10) and write (11) handlers because they delegate
    back here — running them would recurse.
    """
    for n in range(NUM_STEPS):
        if n in _META_STEPS:
            continue
        result = _HANDLERS[n](inputs)
        if not result.ok:
            return ValidateResult(
                ok=False,
                errors=result.errors,
                jump_to=n,
            )
    return ValidateResult(ok=True)


# ---------------------------------------------------------------------------
# Per-step handlers
# ---------------------------------------------------------------------------


def _step_welcome(inputs: dict[str, Any]) -> ValidateResult:
    """Step 0 — no inputs."""
    return ValidateResult(ok=True)


def _step_detect_existing(inputs: dict[str, Any]) -> ValidateResult:
    """Step 1 — if existing OAuth config detected, require explicit
    confirmation that we may replace it.
    """
    has_existing = bool(inputs.get("existing_oauth"))
    confirmed = bool(inputs.get("replace_existing"))
    if has_existing and not confirmed:
        return _err(
            "replace_existing",
            "Confirm you want to replace the existing OAuth configuration.",
        )
    return ValidateResult(ok=True)


def _step_base_url(inputs: dict[str, Any]) -> ValidateResult:
    """Step 2 — public base URL must parse as http(s)://host[:port]."""
    raw = inputs.get("base_url")
    if not isinstance(raw, str) or not raw.strip():
        return _err("base_url", "Base URL is required.")
    parts = urlsplit(raw.strip())
    if parts.scheme not in {"http", "https"}:
        return _err("base_url", "URL must start with http:// or https://.")
    if not parts.netloc:
        return _err("base_url", "URL is missing a host.")
    return ValidateResult(ok=True)


def _step_self_attested(inputs: dict[str, Any]) -> ValidateResult:
    """Steps 3-6 - Google Cloud Console actions the wizard cannot
    verify remotely. Always ok; the gate exists for UX symmetry.
    """
    return ValidateResult(ok=True)


def _step_credentials(inputs: dict[str, Any]) -> ValidateResult:
    """Step 7 — paste credentials. Format checks only; no live
    handshake (out of scope per the design doc).
    """
    errors: list[FieldError] = []
    client_id = inputs.get("client_id")
    if not isinstance(client_id, str) or not client_id.strip():
        errors.append(FieldError("client_id", "Client ID is required."))
    elif not _GOOGLE_CLIENT_ID.fullmatch(client_id.strip()):
        errors.append(
            FieldError(
                "client_id",
                "Client ID must end with .apps.googleusercontent.com.",
            )
        )

    client_secret = inputs.get("client_secret")
    if not isinstance(client_secret, str) or not client_secret:
        errors.append(FieldError("client_secret", "Client secret is required."))
    elif not (CLIENT_SECRET_MIN <= len(client_secret) <= CLIENT_SECRET_MAX):
        errors.append(
            FieldError(
                "client_secret",
                f"Client secret length must be {CLIENT_SECRET_MIN} to {CLIENT_SECRET_MAX} characters.",
            )
        )
    return ValidateResult(ok=not errors, errors=errors)


def _step_linking_policy(inputs: dict[str, Any]) -> ValidateResult:
    """Step 8 — only validates that the input is a boolean."""
    return _bool_field(inputs, "auto_link_verified_emails")


def _step_mfa(inputs: dict[str, Any]) -> ValidateResult:
    """Step 9 — only validates that the input is a boolean."""
    return _bool_field(inputs, "enforce_mfa_on_oauth_signin")


def _step_review(inputs: dict[str, Any]) -> ValidateResult:
    """Step 10 — replays every other step's validation. If anything
    fails here, ``jump_to`` carries the step number so the SPA can
    bounce the user back rather than letting them click Write.
    """
    return validate_all(inputs)


def _step_write(inputs: dict[str, Any]) -> ValidateResult:
    """Step 11 — also re-validates everything. The actual write
    happens in :mod:`regstack.wizard.oauth_google.writer`; this gate
    is the data-correctness check the route layer runs before
    invoking it.
    """
    return validate_all(inputs)


_HANDLERS = (
    _step_welcome,  # 0
    _step_detect_existing,  # 1
    _step_base_url,  # 2
    _step_self_attested,  # 3 — pick GCP project
    _step_self_attested,  # 4 — consent screen
    _step_self_attested,  # 5 — create client ID
    _step_self_attested,  # 6 — paste redirect URI
    _step_credentials,  # 7
    _step_linking_policy,  # 8
    _step_mfa,  # 9
    _step_review,  # 10
    _step_write,  # 11
)
assert len(_HANDLERS) == NUM_STEPS, "handler table out of sync with NUM_STEPS"

# Steps that delegate to validate_all themselves; skipped during
# validate_all to avoid infinite recursion.
_META_STEPS = frozenset({10, 11})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(field_name: str, message: str) -> ValidateResult:
    return ValidateResult(ok=False, errors=[FieldError(field_name, message)])


def _bool_field(inputs: dict[str, Any], name: str) -> ValidateResult:
    value = inputs.get(name, False)
    if not isinstance(value, bool):
        return _err(name, "Must be true or false.")
    return ValidateResult(ok=True)


__all__ = [
    "CLIENT_SECRET_MAX",
    "CLIENT_SECRET_MIN",
    "NUM_STEPS",
    "FieldError",
    "ValidateResult",
    "validate_all",
    "validate_step",
]

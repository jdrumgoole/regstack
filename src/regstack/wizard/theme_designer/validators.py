"""Validation for the theme designer's incoming variable map.

The SPA posts a flat ``{var_name: value}`` dictionary to ``/api/save``;
this module checks the values look sane before they get written into
a ``:root`` block. Validation is intentionally permissive on the
font-stack and shadow shapes (they're free CSS) and strict on the
colour and radius shapes (where typos render the page broken).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Hex colour: #rgb, #rrggbb, or #rrggbbaa. The HTML <input type="color">
# always emits #rrggbb, so the SPA stays inside this set.
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# rgba(r, g, b, a) — accepted because the bundled --rs-accent-bg /
# --rs-danger-bg defaults are rgba() values, and overrides typically
# stay in that shape.
_RGBA = re.compile(
    r"^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+)\s*)?\)$"
)

# A radius like "6px", "0.5rem", "10". Bare numbers are coerced to px
# in the writer.
_RADIUS = re.compile(r"^\d+(?:\.\d+)?(?:px|rem|em)?$")

# A reasonable upper bound on font-stack length so a typo can't blow
# up the file. Real font stacks rarely exceed 200 chars.
_FONT_MAX_LEN = 200


# ---------------------------------------------------------------------------
# Schema — what the SPA is allowed to set
# ---------------------------------------------------------------------------


COLOR_VARS = (
    "--rs-bg",
    "--rs-bg-hover",
    "--rs-surface",
    "--rs-fg",
    "--rs-fg-muted",
    "--rs-border",
    "--rs-accent",
    "--rs-accent-fg",
    "--rs-accent-bg",
    "--rs-danger",
    "--rs-danger-fg",
    "--rs-danger-bg",
)
FONT_VARS = ("--rs-font-display", "--rs-font-body")
RADIUS_VARS = ("--rs-radius",)

ALL_VARS = COLOR_VARS + FONT_VARS + RADIUS_VARS


@dataclass(slots=True, frozen=True)
class FieldError:
    field: str
    message: str


@dataclass(slots=True, frozen=True)
class ValidateResult:
    ok: bool
    errors: list[FieldError] = field(default_factory=list)


def validate_vars(vars_: dict[str, Any], *, scope: str = "light") -> ValidateResult:
    """Validate a ``{var: value}`` map for one colour scheme.

    Args:
        vars_: The variables the SPA wants to write. Unknown keys are
            rejected (catches typos at the API boundary, not in CSS
            output where they'd silently be ignored).
        scope: Either ``"light"`` (the default ``:root`` block) or
            ``"dark"`` (the ``prefers-color-scheme: dark`` block). Only
            used in error messages so the SPA can attach errors to the
            right form section.

    Returns:
        :class:`ValidateResult` with one entry per failing field.
        Empty values (``""`` or ``None``) are treated as "not set" and
        skipped — the writer omits unset vars from the emitted block.
    """
    errors: list[FieldError] = []
    for name, raw in vars_.items():
        if name not in ALL_VARS:
            errors.append(FieldError(name, f"Unknown variable in {scope} scope: {name!r}."))
            continue
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if not isinstance(raw, str):
            errors.append(FieldError(name, f"{name} must be a string."))
            continue
        value = raw.strip()
        if name in COLOR_VARS and not _is_color(value):
            errors.append(
                FieldError(
                    name,
                    f"{name} must be a hex colour (#rgb / #rrggbb) or rgba(...).",
                )
            )
        elif name in FONT_VARS and not _is_font(value):
            errors.append(
                FieldError(
                    name,
                    f"{name} must be a non-empty font stack under {_FONT_MAX_LEN} chars.",
                )
            )
        elif name in RADIUS_VARS and not _is_radius(value):
            errors.append(
                FieldError(
                    name,
                    f"{name} must be a number with optional px/rem/em suffix.",
                )
            )
    return ValidateResult(ok=not errors, errors=errors)


def _is_color(value: str) -> bool:
    return bool(_HEX_COLOR.fullmatch(value) or _RGBA.fullmatch(value))


def _is_font(value: str) -> bool:
    return 0 < len(value) <= _FONT_MAX_LEN


def _is_radius(value: str) -> bool:
    return bool(_RADIUS.fullmatch(value))


__all__ = [
    "ALL_VARS",
    "COLOR_VARS",
    "FONT_VARS",
    "RADIUS_VARS",
    "FieldError",
    "ValidateResult",
    "validate_vars",
]

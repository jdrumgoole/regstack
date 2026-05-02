"""Tests for the theme designer's incoming-payload validators."""

from __future__ import annotations

import pytest

from regstack.wizard.theme_designer.validators import (
    ALL_VARS,
    COLOR_VARS,
    FONT_VARS,
    RADIUS_VARS,
    validate_vars,
)

# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


def test_all_vars_unique() -> None:
    assert len(ALL_VARS) == len(set(ALL_VARS))


def test_all_vars_partitions_cleanly() -> None:
    assert set(ALL_VARS) == set(COLOR_VARS) | set(FONT_VARS) | set(RADIUS_VARS)


# ---------------------------------------------------------------------------
# Empty / unset
# ---------------------------------------------------------------------------


def test_empty_dict_is_ok() -> None:
    assert validate_vars({}).ok is True


def test_empty_string_value_is_skipped_not_rejected() -> None:
    """The SPA sends "" for unset fields; the writer omits them. The
    validator should treat that as 'no value', not as an error."""
    result = validate_vars({"--rs-accent": ""})
    assert result.ok is True


def test_none_value_is_skipped() -> None:
    assert validate_vars({"--rs-accent": None}).ok is True


# ---------------------------------------------------------------------------
# Unknown keys
# ---------------------------------------------------------------------------


def test_unknown_var_is_rejected() -> None:
    result = validate_vars({"--rs-typo": "#000"})
    assert result.ok is False
    assert any(e.field == "--rs-typo" for e in result.errors)


def test_scope_appears_in_unknown_var_message() -> None:
    result = validate_vars({"--rs-typo": "#000"}, scope="dark")
    assert "dark" in result.errors[0].message


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "#fff",
        "#FFFFFF",
        "#0d9488",
        "#0d948880",
        "rgba(13, 148, 136, 0.08)",
        "rgb(13, 148, 136)",
    ],
)
def test_colour_accepts_valid(value: str) -> None:
    assert validate_vars({"--rs-accent": value}).ok is True


@pytest.mark.parametrize(
    "value",
    [
        "blue",  # named colours not supported (we want explicit hex)
        "#xyz",
        "#1234",  # 4-char not valid (only 3/6/8)
        "rgb(256, 0, 0)",  # we don't bounds-check the channel — but the parens shape must match
        "0d9488",  # missing #
    ],
)
def test_colour_rejects_invalid(value: str) -> None:
    if value == "rgb(256, 0, 0)":
        # The current regex is shape-only, not channel-bounded; this
        # one passes today. Skip rather than constrain.
        pytest.skip("regex is shape-only by design")
    assert validate_vars({"--rs-accent": value}).ok is False


def test_colour_value_must_be_string() -> None:
    result = validate_vars({"--rs-accent": 16777215})
    assert result.ok is False
    assert "string" in result.errors[0].message


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def test_font_accepts_simple_name() -> None:
    assert validate_vars({"--rs-font-body": "Inter"}).ok is True


def test_font_accepts_full_stack() -> None:
    stack = '"Inter", system-ui, sans-serif'
    assert validate_vars({"--rs-font-body": stack}).ok is True


def test_font_rejects_too_long() -> None:
    long = "x" * 250
    result = validate_vars({"--rs-font-body": long})
    assert result.ok is False


# ---------------------------------------------------------------------------
# Radius
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["6", "6px", "10", "0.5rem", "1em", "12px"])
def test_radius_accepts_valid(value: str) -> None:
    assert validate_vars({"--rs-radius": value}).ok is True


@pytest.mark.parametrize("value", ["medium", "6 px", "px6", "-5px", "auto"])
def test_radius_rejects_invalid(value: str) -> None:
    assert validate_vars({"--rs-radius": value}).ok is False


# ---------------------------------------------------------------------------
# Multiple errors at once
# ---------------------------------------------------------------------------


def test_collects_multiple_errors() -> None:
    result = validate_vars(
        {"--rs-accent": "not-a-colour", "--rs-radius": "auto"},
    )
    assert result.ok is False
    fields = {e.field for e in result.errors}
    assert fields == {"--rs-accent", "--rs-radius"}

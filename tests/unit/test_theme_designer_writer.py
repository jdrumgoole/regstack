"""Golden-file tests for the theme-designer's CSS writer + reader."""

from __future__ import annotations

from pathlib import Path

from regstack.wizard.theme_designer.writer import (
    THEME_FILE,
    load_theme,
    save_theme,
)

# ---------------------------------------------------------------------------
# save_theme — basic shape
# ---------------------------------------------------------------------------


def test_save_writes_header_comment(tmp_path: Path) -> None:
    save_theme(tmp_path, light={"--rs-accent": "#0d9488"})
    text = (tmp_path / THEME_FILE).read_text()
    assert text.startswith("/* regstack-theme.css")
    assert "regstack theme design" in text


def test_save_emits_root_block_with_only_set_vars(tmp_path: Path) -> None:
    save_theme(
        tmp_path,
        light={"--rs-accent": "#0d9488", "--rs-radius": "10px"},
    )
    text = (tmp_path / THEME_FILE).read_text()
    assert ":root {" in text
    assert "--rs-accent: #0d9488;" in text
    assert "--rs-radius: 10px;" in text
    # Vars NOT in the payload must NOT appear.
    assert "--rs-fg" not in text


def test_save_omits_dark_block_when_dark_empty(tmp_path: Path) -> None:
    save_theme(tmp_path, light={"--rs-accent": "#0d9488"})
    text = (tmp_path / THEME_FILE).read_text()
    assert "prefers-color-scheme" not in text


def test_save_emits_dark_block_when_present(tmp_path: Path) -> None:
    save_theme(
        tmp_path,
        light={"--rs-accent": "#0d9488"},
        dark={"--rs-accent": "#2dd4bf"},
    )
    text = (tmp_path / THEME_FILE).read_text()
    assert "@media (prefers-color-scheme: dark)" in text
    assert "--rs-accent: #2dd4bf;" in text
    # The light value still in there.
    assert "--rs-accent: #0d9488;" in text


def test_save_returns_counts(tmp_path: Path) -> None:
    result = save_theme(
        tmp_path,
        light={"--rs-accent": "#000", "--rs-radius": "10"},
        dark={"--rs-accent": "#fff"},
    )
    assert result.light_count == 2
    assert result.dark_count == 1
    assert result.target_path == (tmp_path / THEME_FILE).resolve()
    assert result.bytes_written > 0


# ---------------------------------------------------------------------------
# Coercion + sanitisation
# ---------------------------------------------------------------------------


def test_save_coerces_bare_radius_to_px(tmp_path: Path) -> None:
    save_theme(tmp_path, light={"--rs-radius": "10"})
    text = (tmp_path / THEME_FILE).read_text()
    assert "--rs-radius: 10px;" in text


def test_save_drops_empty_string_values(tmp_path: Path) -> None:
    save_theme(
        tmp_path,
        light={"--rs-accent": "#000", "--rs-fg": "", "--rs-radius": "  "},
    )
    text = (tmp_path / THEME_FILE).read_text()
    assert "--rs-accent" in text
    assert "--rs-fg" not in text
    assert "--rs-radius" not in text


def test_save_drops_unknown_keys_silently(tmp_path: Path) -> None:
    """Validator catches unknown keys at the API boundary; the writer
    is the second line of defence — silently drop rather than emit
    bogus CSS."""
    save_theme(
        tmp_path,
        light={"--rs-accent": "#000", "--rs-typo": "#fff"},
    )
    text = (tmp_path / THEME_FILE).read_text()
    assert "--rs-typo" not in text


def test_save_creates_target_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "fresh" / "static"
    save_theme(nested, light={"--rs-accent": "#000"})
    assert (nested / THEME_FILE).exists()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_save_is_idempotent(tmp_path: Path) -> None:
    payload = {"--rs-accent": "#0d9488", "--rs-radius": "10px"}
    save_theme(tmp_path, light=payload)
    first = (tmp_path / THEME_FILE).read_text()
    save_theme(tmp_path, light=payload)
    second = (tmp_path / THEME_FILE).read_text()
    assert first == second


# ---------------------------------------------------------------------------
# load_theme — round-trip
# ---------------------------------------------------------------------------


def test_load_returns_empty_when_no_file(tmp_path: Path) -> None:
    assert load_theme(tmp_path) == {"light": {}, "dark": {}}


def test_load_round_trips_light_block(tmp_path: Path) -> None:
    save_theme(
        tmp_path,
        light={"--rs-accent": "#0d9488", "--rs-radius": "10px"},
    )
    state = load_theme(tmp_path)
    assert state["light"] == {"--rs-accent": "#0d9488", "--rs-radius": "10px"}
    assert state["dark"] == {}


def test_load_round_trips_both_blocks(tmp_path: Path) -> None:
    save_theme(
        tmp_path,
        light={"--rs-accent": "#0d9488"},
        dark={"--rs-accent": "#2dd4bf"},
    )
    state = load_theme(tmp_path)
    assert state["light"] == {"--rs-accent": "#0d9488"}
    assert state["dark"] == {"--rs-accent": "#2dd4bf"}


def test_load_handles_corrupt_file(tmp_path: Path) -> None:
    (tmp_path / "regstack-theme.css").write_text("this is not css {")
    state = load_theme(tmp_path)
    assert state == {"light": {}, "dark": {}}

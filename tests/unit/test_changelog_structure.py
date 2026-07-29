"""Structural checks on docs/changelog.md.

The changelog is in the Sphinx toctree and the docs build runs with
``-W``, so MyST's ``myst.header`` warning about a non-consecutive heading
jump is a hard CI failure. That's a slow way to find out: the docs job
takes minutes and only runs after a push, while the mistake itself is a
missing ``###`` between a ``##`` release heading and its ``#### Added``
bullets — which is easy to make when appending in-flight work to
``## Unreleased``.

These parse the file directly, so the same defect fails in milliseconds
during the ordinary test run instead of on the runner.
"""

from __future__ import annotations

import re
from pathlib import Path

_CHANGELOG = Path(__file__).resolve().parents[2] / "docs" / "changelog.md"

# ATX headings only; the changelog uses no setext headings. Skip fenced
# code so a `#` comment inside a block is never read as a heading.
_HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _headings() -> list[tuple[int, str, int]]:
    """Return ``(level, text, line_number)`` for each heading."""
    out: list[tuple[int, str, int]] = []
    in_fence = False
    for lineno, raw in enumerate(_CHANGELOG.read_text(encoding="utf-8").splitlines(), 1):
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(raw)
        if m:
            out.append((len(m.group(1)), m.group(2).strip(), lineno))
    return out


def test_no_heading_level_is_skipped() -> None:
    """A heading may deepen by at most one level at a time.

    MyST emits ``Non-consecutive header level increase`` otherwise, which
    ``-W`` promotes to an error and fails the docs build.
    """
    offenders: list[str] = []
    previous = 0
    for level, text, lineno in _headings():
        if previous and level > previous + 1:
            offenders.append(
                f"line {lineno}: H{previous} -> H{level} at {text!r} "
                f"(insert an H{previous + 1}, e.g. a '### Headline' section)"
            )
        previous = level
    assert not offenders, "changelog skips heading levels:\n  " + "\n  ".join(offenders)


def test_release_sections_are_h2() -> None:
    """Version sections stay at H2 so the toctree renders one level of
    releases rather than promoting one into the page title."""
    versioned = [
        (level, text)
        for level, text, _ in _headings()
        if re.match(r"^\[?\d+\.\d+\.\d+\]? — ", text) or text == "Unreleased"
    ]
    assert versioned, "no version sections found — did the heading format change?"
    wrong = [f"H{level}: {text}" for level, text in versioned if level != 2]
    assert not wrong, f"version sections must be H2: {wrong}"

"""Regression test for the sdist exclude list in pyproject.toml.

Hatchling's sdist default includes every git-tracked file. Without the
``[tool.hatch.build.targets.sdist] exclude`` block the published source
tarball ships internal planning docs, security-review prompts, the full
test suite, CI configuration, and (when built from a worktree) a
``.git`` text file with the developer's absolute home path.

This test reads the configured exclude list straight from ``pyproject.toml``
rather than actually building the sdist, so it stays fast (<10 ms) and
doesn't require a build-time toolchain. The list is small enough that a
literal whitelist of required entries is the right contract — if someone
removes one of these the test names exactly what got dropped.

Flagged as W-2 in the 2026-05-15 / 2026-05-16 security reviews.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _exclude_set() -> set[str]:
    with _PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    sdist = data["tool"]["hatch"]["build"]["targets"]["sdist"]
    return set(sdist.get("exclude", []))


_REQUIRED_EXCLUDES = frozenset(
    {
        ".git",  # worktree-pointer leak
        ".github/",
        ".python-version",
        ".readthedocs.yaml",
        "CLAUDE.md",
        "docs/",
        "examples/",
        "scripts/",
        "tasks.py",
        "tasks/",
        "tests/",
        "uv.lock",
    }
)


def test_sdist_exclude_covers_required_paths() -> None:
    configured = _exclude_set()
    missing = _REQUIRED_EXCLUDES - configured
    assert not missing, (
        f"sdist exclude list dropped required entries: {sorted(missing)} "
        "(re-introduces W-2 from the security review)"
    )


def test_sdist_exclude_block_exists() -> None:
    """If the whole [tool.hatch.build.targets.sdist] section disappears
    the default-include-everything behaviour returns. Catch that
    explicitly so the failure mode is obvious."""
    with _PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    sdist_block = (
        data.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("sdist")
    )
    assert sdist_block is not None, (
        "[tool.hatch.build.targets.sdist] missing from pyproject.toml — "
        "sdist will revert to default-include-everything"
    )

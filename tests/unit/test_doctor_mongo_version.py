"""Tests for the ``regstack doctor`` MongoDB server-version advisory.

Closes the I-3 finding from the 2026-05-20 security review: warn when
the connected MongoDB *server* is below the CVE-2025-14847 patched
baseline. The version-comparison logic is pure, so most coverage is
table-driven and needs no live server."""

from __future__ import annotations

import pytest

from regstack.cli._results import CheckResult
from regstack.cli.doctor import _assess_mongo_server_version, _parse_mongo_version


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("7.0.5", (7, 0, 5)),
        ("8.0.17", (8, 0, 17)),
        ("8.0.0-rc1", (8, 0, 0)),  # pre-release suffix tolerated
        ("4.4.30", (4, 4, 30)),
        ("garbage", None),
        ("8.0", None),  # too few components
        ("8.x.0", None),  # non-integer component
    ],
)
def test_parse_mongo_version(version: str, expected: tuple[int, int, int] | None) -> None:
    assert _parse_mongo_version(version) == expected


@pytest.mark.parametrize(
    "version",
    [
        # Each known LTS track, one below its patched baseline → vulnerable.
        "8.2.2",
        "8.0.16",
        "7.0.27",
        "6.0.26",
        "5.0.31",
        "4.4.29",
    ],
)
def test_assess_flags_affected_versions(version: str) -> None:
    ok, detail = _assess_mongo_server_version(version)
    assert ok is False
    assert "CVE-2025-14847" in detail


@pytest.mark.parametrize(
    "version",
    [
        # Each known track at exactly its patched baseline → safe.
        "8.2.3",
        "8.0.17",
        "7.0.28",
        "6.0.27",
        "5.0.32",
        "4.4.30",
        # And comfortably above.
        "8.0.20",
        "7.0.99",
    ],
)
def test_assess_accepts_patched_versions(version: str) -> None:
    ok, _ = _assess_mongo_server_version(version)
    assert ok is True


@pytest.mark.parametrize("version", ["8.3.0", "9.0.0", "10.1.2"])
def test_assess_accepts_versions_newer_than_advisory(version: str) -> None:
    ok, detail = _assess_mongo_server_version(version)
    assert ok is True
    assert "newer" in detail


@pytest.mark.parametrize("version", ["3.6.0", "4.0.28", "4.2.24"])
def test_assess_warns_on_eol_versions_below_all_tracks(version: str) -> None:
    """Versions older than the oldest advisory track are EOL and below
    every patched release — they must warn, not soft-pass."""
    ok, detail = _assess_mongo_server_version(version)
    assert ok is False
    assert "end-of-life" in detail


@pytest.mark.parametrize("version", ["8.1.5", "7.1.0", "6.1.3"])
def test_assess_soft_passes_unrecognised_rapid_tracks(version: str) -> None:
    """Non-LTS rapid releases between known tracks aren't enumerated by
    the advisory; surface a 'verify manually' note but don't fail."""
    ok, detail = _assess_mongo_server_version(version)
    assert ok is True
    assert "verify against CVE-2025-14847" in detail


def test_assess_unparseable_version_does_not_fail() -> None:
    ok, detail = _assess_mongo_server_version("not-a-version")
    assert ok is True
    assert "could not parse" in detail


def test_checkresult_warned_is_advisory_not_failure() -> None:
    """A warned result must not count as a failure (ok=True) but must be
    distinguishable (warn=True) so doctor can render it as ⚠."""
    r = CheckResult.warned("mongo server", "out of date")
    assert r.ok is True
    assert r.warn is True
    assert r.skipped is False

"""Pin the GH-issue-#71 coverage-resilience changes.

Two surfaces:

- ``scripts/ccr_coverage_setup.py`` — per-service ``StepResult``
  tolerance + the ``_backends_available`` mapping.
- ``tasks.py`` — the ``_resolve_coverage_backends`` helper that
  underpins ``inv coverage --backends=...``.

These are pure functions, so the tests stay unit-shaped (no
subprocess, no apt, no Docker)."""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(module_name: str, relative_path: str) -> Any:
    """Import a top-level file (``tasks.py``, ``scripts/ccr_*.py``) by
    path so the tests can run without polluting ``sys.modules`` with
    project-root modules in a way that confuses pytest collection."""
    full = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, full)
    assert spec is not None and spec.loader is not None, full
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# --- scripts/ccr_coverage_setup.py -----------------------------------------


@pytest.fixture(scope="module")
def setup_mod() -> Any:
    return _load("regstack_ccr_setup_under_test", "scripts/ccr_coverage_setup.py")


def test_step_result_carries_name_ok_detail(setup_mod: Any) -> None:
    r = setup_mod.StepResult(name="MongoDB", ok=True, detail="listening on :27017")
    assert (r.name, r.ok, r.detail) == ("MongoDB", True, "listening on :27017")


def test_backends_available_sqlite_only_when_nothing_succeeded(setup_mod: Any) -> None:
    """The whole point of the #71 fix: even when mongo + postgres
    couldn't be installed, the routine must report `sqlite` as the
    available backend so `inv coverage --backends=sqlite` can produce
    a number."""
    failed_mongo = setup_mod.StepResult(name="MongoDB", ok=False, detail="apt blocked")
    failed_pg = setup_mod.StepResult(name="PostgreSQL", ok=False, detail="apt blocked")
    assert setup_mod._backends_available([failed_mongo, failed_pg]) == ["sqlite"]


def test_backends_available_includes_each_ok_service(setup_mod: Any) -> None:
    ok_mongo = setup_mod.StepResult(name="MongoDB", ok=True, detail="ok")
    failed_pg = setup_mod.StepResult(name="PostgreSQL", ok=False, detail="apt blocked")
    assert setup_mod._backends_available([ok_mongo, failed_pg]) == ["sqlite", "mongo"]

    ok_mongo = setup_mod.StepResult(name="MongoDB", ok=True, detail="ok")
    ok_pg = setup_mod.StepResult(name="PostgreSQL", ok=True, detail="ok")
    assert setup_mod._backends_available([ok_mongo, ok_pg]) == ["sqlite", "mongo", "postgres"]


def test_backends_available_ignores_playwright(setup_mod: Any) -> None:
    """Playwright is for the e2e suite, not the backend matrix."""
    pw = setup_mod.StepResult(name="Playwright", ok=True, detail="ok")
    assert setup_mod._backends_available([pw]) == ["sqlite"]


# --- MongoDB static-tarball fallback (the 403-on-apt-repo path) ------------


def test_mongo_tarball_url_shape(setup_mod: Any) -> None:
    url = setup_mod._mongo_tarball_url("7.0.14", "x86_64", "ubuntu2004")
    assert url == ("https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-ubuntu2004-7.0.14.tgz")


def test_pinned_distro_tag_has_builds_for_both_arches(setup_mod: Any) -> None:
    """Guard the pin: the chosen distro tag must be one MongoDB actually
    ships for both arches. 7.0.x has no ubuntu2404 build and no Debian
    aarch64 build, so a stale pin there would 404 the fallback at runtime
    (verified against fastdl.mongodb.org on 2026-06-22). ubuntu2004 is the
    oldest-glibc tag with both arches, so the binary also runs forward on
    newer containers."""
    assert setup_mod.MONGO_TARBALL_DISTRO == "ubuntu2004"


def test_install_mongod_from_tarball_bails_on_unsupported_arch(
    setup_mod: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown arch returns False without ever shelling out to curl/tar."""
    monkeypatch.setattr(setup_mod.platform, "machine", lambda: "riscv64")
    called = False

    def _boom(*_a: Any, **_k: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("must not run subprocesses on unsupported arch")

    monkeypatch.setattr(setup_mod, "_run", _boom)
    assert setup_mod._install_mongod_from_tarball() is False
    assert called is False


def test_tarball_sha256_pins_cover_both_supported_arches(setup_mod: Any) -> None:
    """Every arch the installer accepts must have a pinned hash, else the
    download can't be verified and the install is refused. The two pins
    must be distinct 64-char hex digests."""
    pins = setup_mod.MONGO_TARBALL_SHA256
    assert set(pins) == {"x86_64", "aarch64"}
    for sha in pins.values():
        assert len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
    assert pins["x86_64"] != pins["aarch64"]


def test_sha256_file_matches_hashlib(setup_mod: Any, tmp_path: Path) -> None:
    """The streamed hasher agrees with a one-shot hashlib digest."""
    import hashlib

    blob = b"regstack-mongo-tarball" * 100_000  # exceed the 1 MiB chunk
    f = tmp_path / "blob.bin"
    f.write_bytes(blob)
    assert setup_mod._sha256_file(f) == hashlib.sha256(blob).hexdigest()


def test_install_mongod_refuses_on_checksum_mismatch(
    setup_mod: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A downloaded tarball whose hash doesn't match the pin is rejected
    before extraction — `tar` and `sudo install` never run."""
    monkeypatch.setattr(setup_mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(setup_mod, "_have", lambda _cmd: True)
    monkeypatch.setattr(setup_mod.tempfile, "mkdtemp", lambda **_k: str(tmp_path))

    ran: list[str] = []

    def _fake_run(cmd: list[str], **_k: Any) -> Any:
        ran.append(cmd[0])
        if cmd[0] == "curl":
            # Simulate the CDN handing us a tampered/corrupt tarball.
            (tmp_path / "mongodb.tgz").write_bytes(b"not the real tarball")
            return None
        raise AssertionError(f"must not run {cmd[0]!r} after a checksum mismatch")

    monkeypatch.setattr(setup_mod, "_run", _fake_run)
    assert setup_mod._install_mongod_from_tarball() is False
    assert ran == ["curl"]  # downloaded, then stopped — no tar, no install


# --- tasks.py::_resolve_coverage_backends ----------------------------------


@pytest.fixture(scope="module")
def tasks_mod() -> Any:
    return _load("regstack_tasks_under_test", "tasks.py")


def test_resolve_backends_sqlite_only_always_works(tasks_mod: Any) -> None:
    """Sqlite has no port and is always considered usable."""
    usable, excluded = tasks_mod._resolve_coverage_backends("sqlite")
    assert usable == ["sqlite"]
    assert excluded == []


def test_resolve_backends_rejects_unknown(tasks_mod: Any) -> None:
    with pytest.raises(Exception, match="Unknown backend"):
        tasks_mod._resolve_coverage_backends("sqlite,mariadb")


def _close(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def test_resolve_backends_excludes_when_port_closed(
    tasks_mod: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When mongo's port isn't open, it lands in `excluded`. Patching
    ``_port_open`` to a fixed return covers the partial-matrix branch
    without depending on the developer's local services being on/off."""
    monkeypatch.setattr(tasks_mod, "_port_open", lambda port: False)
    usable, excluded = tasks_mod._resolve_coverage_backends("sqlite,mongo,postgres")
    assert usable == ["sqlite"]
    assert sorted(excluded) == ["mongo", "postgres"]


def test_resolve_backends_includes_when_port_open(
    tasks_mod: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tasks_mod, "_port_open", lambda port: True)
    usable, excluded = tasks_mod._resolve_coverage_backends("sqlite,mongo,postgres")
    assert usable == ["sqlite", "mongo", "postgres"]
    assert excluded == []


def test_resolve_backends_respects_subset_request(
    tasks_mod: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--backends=mongo alone returns just mongo (sqlite is not implicitly added)."""
    monkeypatch.setattr(tasks_mod, "_port_open", lambda port: True)
    usable, excluded = tasks_mod._resolve_coverage_backends("mongo")
    assert usable == ["mongo"]
    assert excluded == []

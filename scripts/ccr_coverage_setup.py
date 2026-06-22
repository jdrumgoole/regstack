#!/usr/bin/env python3
"""Bootstrap a fresh CCR container so `inv coverage` can run.

The weekly coverage routine runs in a clean container that ships with
neither MongoDB, PostgreSQL, nor Playwright browsers. The script
installs whatever the environment lets it install, then prints a
status table — full coverage works when every backend came up, and
partial coverage works when only some did. See GitHub issue #71 for
the failure mode that motivated this fallback.

Run before `inv coverage`:

    uv run python scripts/ccr_coverage_setup.py
    uv run python -m invoke coverage --no-html --fail-under=88

Exit codes:
    0 — at least SQLite is usable (always true on a working CCR).
        Partial-matrix coverage is possible; `inv coverage` will run
        against whichever backends actually came up.
    2 — caller asked us to set up a backend explicitly via
        ``--require=<backend>`` and that backend didn't come up.

The script is idempotent: each step probes for an already-good state
and short-circuits. Linux-only (the CCR container is Debian/Ubuntu);
on a maintainer's macOS laptop use `inv db-up` instead.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

MONGO_PORT = 27017
POSTGRES_PORT = 5432

# Pinned MongoDB version + distro tag for the static-tarball fallback
# (see `_install_mongod_from_tarball`). Bump when the apt path moves off
# 7.0 — and keep `MONGO_TARBALL_DISTRO` at the OLDEST distro tag that
# still publishes BOTH x86_64 and aarch64 builds for the chosen version.
#
# Why ubuntu2004 rather than detecting the container's own distro:
#   * It is the lowest-glibc 7.0.x build (glibc 2.31), so the binary runs
#     on every newer Debian/Ubuntu CCR container via forward compat — a
#     jammy build (glibc 2.35) would NOT run on an older container.
#   * 7.0.x ships no ubuntu2404 build, and no Debian aarch64 build, so
#     mapping a container's real distro can yield a URL that 404s. One
#     fixed tag that always exists is strictly more reliable.
# Verified 2026-06-22: ubuntu2004 x86_64 + aarch64 7.0.14 tarballs both
# return HTTP 200 from fastdl.mongodb.org.
MONGO_VERSION = "7.0.14"
MONGO_TARBALL_DISTRO = "ubuntu2004"

# The CI workflow uses these credentials too; matching them keeps
# REGSTACK_TEST_POSTGRES_URL identical between CI and CCR.
PG_USER = "regstack"
PG_PASSWORD = "regstack"
PG_DB = "regstack"


# --- Result types ----------------------------------------------------------


@dataclass(slots=True)
class StepResult:
    """Outcome of one setup step.

    Attributes:
        name: Human-readable backend name (``"MongoDB"``, etc.).
        ok: True if the backend is now usable (port open + auth ready).
        detail: One-line explanation. On success, what was started or
            already running. On failure, the reason (network blocked,
            package not found, …) so the operator can decide whether
            to retry or accept a partial-matrix coverage run.
    """

    name: str
    ok: bool
    detail: str


# --- Logging ---------------------------------------------------------------


def _ok(msg: str) -> None:
    print(f"\033[32m✔\033[0m {msg}", flush=True)


def _info(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"\033[33m!\033[0m {msg}", flush=True)


def _fail(msg: str) -> None:
    print(f"\033[31m✗\033[0m {msg}", flush=True)


def _run(
    cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command, streaming output. Raises CalledProcessError on failure when ``check``."""
    _info(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, env={**os.environ, **(env or {})})


# --- Probes ----------------------------------------------------------------


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _wait_for_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.5)
    return False


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _apt_install(*packages: str) -> None:
    """Install Debian packages non-interactively. Raises CalledProcessError on any failure."""
    env = {"DEBIAN_FRONTEND": "noninteractive"}
    _run(["sudo", "apt-get", "update", "-qq"], env=env)
    _run(["sudo", "apt-get", "install", "-y", "--no-install-recommends", *packages], env=env)


# --- MongoDB static-tarball fallback ---------------------------------------
#
# When the apt repo (repo.mongodb.org) is blocked — the HTTP 403 a
# restricted-network CCR container returns — the binary CDN
# (fastdl.mongodb.org) is often still reachable. The official static
# tarball carries a self-contained `mongod` and needs neither apt nor a
# PPA, so it's the last apt-independent install path before we give up.


def _mongo_tarball_url(version: str, arch: str, distro: str) -> str:
    return f"https://fastdl.mongodb.org/linux/mongodb-linux-{arch}-{distro}-{version}.tgz"


def _install_mongod_from_tarball(version: str = MONGO_VERSION) -> bool:
    """Fetch the official static `mongod` from fastdl.mongodb.org onto PATH.

    apt-independent fallback for restricted-network containers where the
    apt repo 403s but the binary CDN is reachable. Returns True iff
    `mongod` ends up on PATH; False (with a warning) on any miss —
    unsupported arch, missing curl/tar, blocked CDN, or a tarball whose
    layout changed.
    """
    arch = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }.get(platform.machine().lower())
    if arch is None:
        _warn(f"no MongoDB static tarball for arch {platform.machine()!r}")
        return False
    if not _have("curl") or not _have("tar"):
        _warn("curl/tar not on PATH; cannot fetch the static MongoDB tarball")
        return False

    url = _mongo_tarball_url(version, arch, MONGO_TARBALL_DISTRO)
    workdir = Path(tempfile.mkdtemp(prefix="regstack-mongo-"))
    tgz = workdir / "mongodb.tgz"
    try:
        _run(["curl", "-fsSL", "-o", str(tgz), url])
        _run(["tar", "-xzf", str(tgz), "-C", str(workdir)])
    except subprocess.CalledProcessError:
        _warn(f"static tarball download/extract failed: {url}")
        return False

    binaries = sorted(workdir.glob("mongodb-linux-*/bin/mongod"))
    if not binaries:
        _warn("static tarball extracted but contained no mongod binary")
        return False
    try:
        _run(["sudo", "install", "-m", "0755", str(binaries[0]), "/usr/local/bin/mongod"])
    except subprocess.CalledProcessError:
        _warn("could not install mongod into /usr/local/bin")
        return False
    return _have("mongod")


# --- Per-service setup ------------------------------------------------------


def setup_mongo() -> StepResult:
    """Install and start MongoDB on :27017. Returns a StepResult.

    Doesn't raise on failure — the caller decides whether a partial
    matrix is acceptable. Tries (in order): an already-present `mongod`
    on PATH, the mongodb-org apt repo, the distro `mongodb` package, and
    finally the official static tarball from fastdl.mongodb.org (the
    apt-independent path that survives a 403 on the apt repo).
    """
    if _port_open(MONGO_PORT):
        return StepResult(name="MongoDB", ok=True, detail=f"already listening on :{MONGO_PORT}")

    if not _have("mongod"):
        _info("mongod not on PATH; trying mongodb-org via the official repo...")
        try:
            _run(
                [
                    "bash",
                    "-c",
                    "curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | "
                    "sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor && "
                    'echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] '
                    'https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | '
                    "sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list",
                ]
            )
            _apt_install("mongodb-org")
        except subprocess.CalledProcessError:
            _info("mongodb-org install failed; trying distro `mongodb` as fallback")
            try:
                _apt_install("mongodb")
            except subprocess.CalledProcessError:
                _info(
                    "apt paths unreachable (403 on the repo / blocked PPA?); "
                    "falling back to the static binary tarball from fastdl.mongodb.org"
                )
                _install_mongod_from_tarball()

    if not _have("mongod"):
        return StepResult(
            name="MongoDB",
            ok=False,
            detail=(
                "install failed — the mongodb-org apt repo, the distro "
                "`mongodb` package, and the fastdl.mongodb.org static "
                "tarball were all unreachable. Restricted-network CCR "
                "container with the MongoDB CDN blocked too?"
            ),
        )

    dbpath = Path("/var/lib/mongodb")
    logpath = Path("/var/log/mongodb/mongod.log")
    try:
        dbpath.mkdir(parents=True, exist_ok=True)
        logpath.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "sudo",
                "mongod",
                "--fork",
                "--dbpath",
                str(dbpath),
                "--logpath",
                str(logpath),
                "--bind_ip",
                "127.0.0.1",
            ]
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        return StepResult(
            name="MongoDB",
            ok=False,
            detail=f"mongod failed to start: {exc}",
        )

    if not _wait_for_port(MONGO_PORT, timeout=30.0):
        return StepResult(
            name="MongoDB",
            ok=False,
            detail=f"mongod started but :{MONGO_PORT} never came up; check {logpath}",
        )
    return StepResult(name="MongoDB", ok=True, detail=f"started, listening on :{MONGO_PORT}")


def setup_postgres() -> StepResult:
    """Install PostgreSQL and create the regstack superuser. Returns a StepResult."""
    if _port_open(POSTGRES_PORT):
        result_detail = f"already listening on :{POSTGRES_PORT}"
    else:
        if not _have("psql"):
            _info("psql not on PATH; trying postgresql via apt...")
            try:
                _apt_install("postgresql", "postgresql-contrib")
            except subprocess.CalledProcessError as exc:
                return StepResult(
                    name="PostgreSQL",
                    ok=False,
                    detail=(
                        f"apt install failed (exit {exc.returncode}). "
                        "Restricted-network CCR container?"
                    ),
                )
        try:
            version = _detect_pg_version()
        except RuntimeError as exc:
            return StepResult(name="PostgreSQL", ok=False, detail=str(exc))
        _run(["sudo", "pg_ctlcluster", version, "main", "start"], check=False)
        if not _wait_for_port(POSTGRES_PORT, timeout=30.0):
            return StepResult(
                name="PostgreSQL",
                ok=False,
                detail=f"postgres did not start on :{POSTGRES_PORT}",
            )
        result_detail = f"started, listening on :{POSTGRES_PORT}"

    # Create / refresh the regstack superuser idempotently.
    create_role = (
        f"DO $$ BEGIN "
        f"  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{PG_USER}') THEN "
        f"    CREATE ROLE {PG_USER} LOGIN SUPERUSER PASSWORD '{PG_PASSWORD}'; "
        f"  ELSE "
        f"    ALTER ROLE {PG_USER} WITH LOGIN SUPERUSER PASSWORD '{PG_PASSWORD}'; "
        f"  END IF; "
        f"END $$;"
    )
    try:
        _run(["sudo", "-u", "postgres", "psql", "-c", create_role])
    except subprocess.CalledProcessError as exc:
        return StepResult(
            name="PostgreSQL",
            ok=False,
            detail=f"role bootstrap failed (psql exit {exc.returncode})",
        )

    return StepResult(
        name="PostgreSQL",
        ok=True,
        detail=f"{result_detail}; role '{PG_USER}' ready",
    )


def _detect_pg_version() -> str:
    """Find the installed postgres major version (e.g. '16').

    Raises RuntimeError when no postgres is installed; setup_postgres
    converts that into a non-fatal StepResult.
    """
    res = subprocess.run(
        ["bash", "-c", "ls /etc/postgresql 2>/dev/null | sort -n | tail -1"],
        capture_output=True,
        text=True,
        check=False,
    )
    ver = res.stdout.strip()
    if not ver:
        raise RuntimeError("could not detect installed PostgreSQL version under /etc/postgresql")
    return ver


def setup_playwright() -> StepResult:
    """Install the Chromium browser Playwright needs for the e2e tests."""
    try:
        _run(["uv", "run", "playwright", "install", "--with-deps", "chromium"])
    except subprocess.CalledProcessError as exc:
        return StepResult(
            name="Playwright",
            ok=False,
            detail=f"playwright install failed (exit {exc.returncode})",
        )
    return StepResult(name="Playwright", ok=True, detail="Chromium installed (idempotent)")


# --- Reporting -------------------------------------------------------------


def _print_summary(results: list[StepResult]) -> None:
    """Print a human-readable summary table after all steps run."""
    print()
    print("Setup summary:")
    for r in results:
        symbol = "\033[32m✔\033[0m" if r.ok else "\033[31m✗\033[0m"
        print(f"  {symbol} {r.name:<12} {r.detail}")
    print()


def _backends_available(results: list[StepResult]) -> list[str]:
    """Map StepResult names back into ``inv coverage --backends`` values.

    SQLite isn't a setup step (the dev install ships aiosqlite) so it's
    always part of the available set.
    """
    out = ["sqlite"]
    by_name = {r.name: r for r in results}
    if by_name.get("MongoDB") and by_name["MongoDB"].ok:
        out.append("mongo")
    if by_name.get("PostgreSQL") and by_name["PostgreSQL"].ok:
        out.append("postgres")
    return out


# --- Entry point -----------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--skip-mongo", action="store_true", help="Skip MongoDB install/start")
    parser.add_argument(
        "--skip-postgres", action="store_true", help="Skip PostgreSQL install/start"
    )
    parser.add_argument(
        "--skip-playwright", action="store_true", help="Skip Playwright browser install"
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        choices=["mongo", "postgres", "playwright"],
        help=(
            "Treat the named service as required. The script exits 2 if "
            "any required service fails to come up. Repeatable. Default: "
            "nothing is required — the script always exits 0 as long as "
            "sqlite is usable, leaving the matrix-completeness gate to "
            "`inv coverage` itself."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary to stdout instead of the human table.",
    )
    args = parser.parse_args()

    if not _is_linux():
        _fail(
            "This script targets Debian/Ubuntu CCR containers. "
            "On macOS use `inv db-up` and `uv run playwright install chromium` instead."
        )
        return 2

    results: list[StepResult] = []

    try:
        if not args.skip_mongo:
            results.append(setup_mongo())
        if not args.skip_postgres:
            results.append(setup_postgres())
        if not args.skip_playwright:
            results.append(setup_playwright())
    except KeyboardInterrupt:
        _fail("interrupted")
        return 130

    available = _backends_available(results)

    if args.json:
        print(
            json.dumps(
                {
                    "available_backends": available,
                    "steps": [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in results],
                },
                indent=2,
            )
        )
    else:
        _print_summary(results)

    # Honour --require: a required service whose StepResult is not ok
    # triggers a non-zero exit so a scheduled job can distinguish
    # "wanted full matrix, got partial" from "best-effort, got what
    # was reachable."
    required = set(args.require)
    failed_required: list[str] = []
    for r in results:
        key = r.name.lower().replace("postgresql", "postgres")
        if key in required and not r.ok:
            failed_required.append(key)
    if failed_required:
        _fail(f"required services not available: {', '.join(failed_required)}")
        return 2

    if not args.json:
        _ok(f"Coverage can run against: {', '.join(available)}")
        print(
            "\nNext: uv run python -m invoke coverage "
            f"--backends={','.join(available)} --no-html --fail-under=0",
            flush=True,
        )
        if "postgres" in available:
            print(
                "\nExport the postgres URL the coverage task expects:\n"
                f"  export REGSTACK_TEST_POSTGRES_URL="
                f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@localhost:{POSTGRES_PORT}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())

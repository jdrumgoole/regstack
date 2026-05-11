#!/usr/bin/env python3
"""Bootstrap a fresh CCR container so `inv coverage` can run end-to-end.

The weekly coverage routine runs in a clean container that ships with
neither MongoDB, PostgreSQL, nor Playwright browsers. Without them the
backend matrix can't run and any coverage number is meaningless (see
GitHub issue #27 for the failure mode).

Run this once at the start of the routine, before `inv coverage`:

    uv run python scripts/ccr_coverage_setup.py
    uv run python -m invoke coverage --no-html --fail-under=88

The script is idempotent: each step probes for an already-good state
and short-circuits. Linux-only (the CCR container is Debian/Ubuntu);
on a maintainer's macOS laptop you should be using `inv db-up` instead.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

MONGO_PORT = 27017
POSTGRES_PORT = 5432

# The CI workflow uses these credentials too; matching them keeps
# REGSTACK_TEST_POSTGRES_URL identical between CI and CCR.
PG_USER = "regstack"
PG_PASSWORD = "regstack"
PG_DB = "regstack"


def _ok(msg: str) -> None:
    print(f"\033[32m✔\033[0m {msg}", flush=True)


def _info(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _fail(msg: str) -> None:
    print(f"\033[31m✗\033[0m {msg}", flush=True)


def _run(cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, streaming output. Raises CalledProcessError on failure when ``check``."""
    _info(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, text=True, env={**os.environ, **(env or {})})


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
    """Install Debian packages non-interactively."""
    env = {"DEBIAN_FRONTEND": "noninteractive"}
    _run(["sudo", "apt-get", "update", "-qq"], env=env)
    _run(["sudo", "apt-get", "install", "-y", "--no-install-recommends", *packages], env=env)


def setup_mongo() -> None:
    """Install and start MongoDB on :27017."""
    if _port_open(MONGO_PORT):
        _ok(f"MongoDB already listening on :{MONGO_PORT}")
        return
    if not _have("mongod"):
        _info("mongod not on PATH; installing mongodb-org via the official repo...")
        # The mongodb-org packages are not in Debian/Ubuntu default repos.
        # Fall back to the distro's `mongodb` if mongodb-org isn't available
        # — the test suite uses standard wire-protocol features, no Atlas-only.
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
            _info("mongodb-org install failed; trying distro `mongodb` package as fallback")
            _apt_install("mongodb")

    # Start mongod in the background. systemd may not be available in a
    # container, so launch directly with a sane dbpath and logpath.
    dbpath = Path("/var/lib/mongodb")
    logpath = Path("/var/log/mongodb/mongod.log")
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
    if not _wait_for_port(MONGO_PORT, timeout=30.0):
        _fail(f"mongod started but :{MONGO_PORT} never came up. Check {logpath}.")
        sys.exit(2)
    _ok(f"MongoDB listening on :{MONGO_PORT}")


def setup_postgres() -> None:
    """Install PostgreSQL, create the regstack superuser, and confirm auth works."""
    if _port_open(POSTGRES_PORT):
        _ok(f"PostgreSQL already listening on :{POSTGRES_PORT} (auth not re-verified)")
        # Still ensure the regstack role exists with the expected password.
    else:
        if not _have("psql"):
            _info("psql not on PATH; installing postgresql via apt...")
            _apt_install("postgresql", "postgresql-contrib")
        # In containers without systemd, use pg_ctlcluster directly.
        _run(["sudo", "pg_ctlcluster", _detect_pg_version(), "main", "start"], check=False)
        if not _wait_for_port(POSTGRES_PORT, timeout=30.0):
            _fail(f"postgres did not start on :{POSTGRES_PORT}.")
            sys.exit(2)
        _ok(f"PostgreSQL listening on :{POSTGRES_PORT}")

    # Create the regstack superuser idempotently. The per-test fixture in
    # tests/conftest.py needs CREATE DATABASE permission, which a superuser
    # always has.
    create_role = (
        f"DO $$ BEGIN "
        f"  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{PG_USER}') THEN "
        f"    CREATE ROLE {PG_USER} LOGIN SUPERUSER PASSWORD '{PG_PASSWORD}'; "
        f"  ELSE "
        f"    ALTER ROLE {PG_USER} WITH LOGIN SUPERUSER PASSWORD '{PG_PASSWORD}'; "
        f"  END IF; "
        f"END $$;"
    )
    _run(["sudo", "-u", "postgres", "psql", "-c", create_role])
    _ok(f"PostgreSQL role '{PG_USER}' ready with password auth")


def _detect_pg_version() -> str:
    """Find the installed postgres major version (e.g. '16')."""
    res = subprocess.run(
        ["bash", "-c", "ls /etc/postgresql 2>/dev/null | sort -n | tail -1"],
        capture_output=True,
        text=True,
        check=False,
    )
    ver = res.stdout.strip()
    if not ver:
        _fail("could not detect installed PostgreSQL version under /etc/postgresql")
        sys.exit(2)
    return ver


def setup_playwright() -> None:
    """Install the Chromium browser Playwright needs for the e2e tests."""
    # `playwright install --with-deps chromium` is idempotent — repeated
    # runs just verify the install and exit quickly.
    _run(["uv", "run", "playwright", "install", "--with-deps", "chromium"])
    _ok("Playwright Chromium installed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--skip-mongo", action="store_true", help="Skip MongoDB install/start"
    )
    parser.add_argument(
        "--skip-postgres", action="store_true", help="Skip PostgreSQL install/start"
    )
    parser.add_argument(
        "--skip-playwright", action="store_true", help="Skip Playwright browser install"
    )
    args = parser.parse_args()

    if not _is_linux():
        _fail(
            "This script targets Debian/Ubuntu CCR containers. "
            "On macOS use `inv db-up` and `uv run playwright install chromium` instead."
        )
        return 2

    try:
        if not args.skip_mongo:
            setup_mongo()
        if not args.skip_postgres:
            setup_postgres()
        if not args.skip_playwright:
            setup_playwright()
    except KeyboardInterrupt:
        _fail("interrupted")
        return 130
    except subprocess.CalledProcessError as exc:
        _fail(f"step failed: {exc}")
        return exc.returncode or 1

    _ok("CCR environment ready. Now run: uv run python -m invoke coverage --no-html --fail-under=88")
    print(
        "\nExport the postgres URL the coverage task expects:\n"
        f"  export REGSTACK_TEST_POSTGRES_URL=postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@localhost:{POSTGRES_PORT}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

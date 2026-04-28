from __future__ import annotations

import shutil
import socket
import subprocess
import time

from invoke import Context, task


@task
def install(c: Context) -> None:
    """Sync dependencies including dev extras."""
    c.run("uv sync --extra dev", pty=True)


@task
def lint(c: Context) -> None:
    """Run ruff + mypy."""
    c.run("uv run ruff check src tests", pty=True)
    c.run("uv run ruff format --check src tests", pty=True)
    c.run("uv run mypy src", pty=True)


@task
def fmt(c: Context) -> None:
    """Apply ruff formatting and lint fixes."""
    c.run("uv run ruff format src tests", pty=True)
    c.run("uv run ruff check --fix src tests", pty=True)


_DEFAULT_PG_URL = "postgresql+asyncpg://postgres@localhost:5432"


def _pytest(c: Context, *, env: dict | None = None, k: str = "", verbose: bool = False) -> None:
    flags = ["uv run python -m pytest", "-n", "auto"]
    if verbose:
        flags.append("-vv")
    if k:
        flags.extend(["-k", k])
    c.run(" ".join(flags), pty=True, env=env)


@task
def test(c: Context, k: str = "", verbose: bool = False) -> None:
    """Run the full parallel test suite (sqlite + mongo by default)."""
    _pytest(c, k=k, verbose=verbose)


@task(name="test-sqlite")
def test_sqlite(c: Context, k: str = "", verbose: bool = False) -> None:
    """Run only the SQLite parametrization. No external services needed."""
    _pytest(c, env={"REGSTACK_TEST_BACKENDS": "sqlite"}, k=k, verbose=verbose)


@task(name="test-mongo")
def test_mongo(c: Context, k: str = "", verbose: bool = False) -> None:
    """Run only the MongoDB parametrization (needs local mongo on :27017)."""
    _pytest(c, env={"REGSTACK_TEST_BACKENDS": "mongo"}, k=k, verbose=verbose)


@task(name="test-postgres")
def test_postgres(
    c: Context, k: str = "", verbose: bool = False, url: str = _DEFAULT_PG_URL
) -> None:
    """Run only the Postgres parametrization (needs local postgres on :5432).

    Override the connection with --url=postgresql+asyncpg://user:pw@host:port.
    The user must have CREATE DATABASE permission; the per-test fixture
    creates a fresh database per test and drops it on teardown.
    """
    env = {
        "REGSTACK_TEST_BACKENDS": "postgres",
        "REGSTACK_TEST_POSTGRES_URL": url,
    }
    _pytest(c, env=env, k=k, verbose=verbose)


@task(name="test-all")
def test_all(c: Context, verbose: bool = False, pg_url: str = _DEFAULT_PG_URL) -> None:
    """Run the suite against all three backends. Needs local mongo + postgres."""
    env = {
        "REGSTACK_TEST_BACKENDS": "sqlite,mongo,postgres",
        "REGSTACK_TEST_POSTGRES_URL": pg_url,
    }
    _pytest(c, env=env, verbose=verbose)


@task(name="test-serial")
def test_serial(c: Context, k: str = "") -> None:
    """Run tests serially (useful for diagnosing flakes)."""
    cmd = "uv run python -m pytest -vv"
    if k:
        cmd += f" -k {k}"
    c.run(cmd, pty=True)


@task
def coverage(
    c: Context,
    pg_url: str = _DEFAULT_PG_URL,
    html: bool = True,
    fail_under: int = 0,
) -> None:
    """Run the full backend matrix under coverage.

    Combines per-worker .coverage.* files (pytest-xdist runs one
    coverage instance per worker; settings.parallel = true plus
    `coverage combine` glues them back together) and prints the
    line-coverage report. With --html (default), also writes an
    HTML report under ``htmlcov/``.

    Set ``--fail-under=N`` to make the task exit non-zero when total
    line coverage drops below N percent — useful in CI.
    """
    # Wipe stale coverage state so partial reruns don't leave double-counted
    # data files behind.
    c.run("uv run coverage erase", pty=True, warn=True)
    env = {
        "REGSTACK_TEST_BACKENDS": "sqlite,mongo,postgres",
        "REGSTACK_TEST_POSTGRES_URL": pg_url,
        # pytest-cov picks settings up from [tool.coverage.*] in pyproject.toml.
        "COVERAGE_PROCESS_START": "pyproject.toml",
    }
    c.run(
        "uv run python -m pytest -n auto --cov=src/regstack --cov-report=",
        pty=True,
        env=env,
    )
    c.run("uv run coverage combine", pty=True, warn=True)
    report_cmd = "uv run coverage report"
    if fail_under > 0:
        report_cmd += f" --fail-under={fail_under}"
    c.run(report_cmd, pty=True)
    if html:
        c.run("uv run coverage html", pty=True)
        print("\nHTML report: htmlcov/index.html")


@task
def e2e(c: Context) -> None:
    """Run Playwright end-to-end suite."""
    c.run("uv run python -m pytest -m e2e -vv", pty=True)


@task(name="run-example")
def run_example(c: Context) -> None:
    """Boot the minimal example app on port 8000."""
    with c.cd("examples/minimal"):
        c.run("uv run uvicorn main:app --reload --port 8000", pty=True)


@task
def build(c: Context) -> None:
    """Build sdist + wheel."""
    c.run("uv build", pty=True)


@task
def clean(c: Context) -> None:
    """Remove build artefacts."""
    c.run(
        "rm -rf build dist *.egg-info .pytest_cache .mypy_cache "
        ".ruff_cache docs/_build docs/_autosummary",
        pty=True,
    )


@task
def docs(c: Context, warning_as_error: bool = True) -> None:
    """Build the Sphinx HTML docs into docs/_build/html."""
    flags = "-W --keep-going" if warning_as_error else ""
    c.run(
        f"uv run sphinx-build -b html {flags} docs docs/_build/html",
        pty=True,
    )


@task(name="docs-clean")
def docs_clean(c: Context) -> None:
    """Drop generated doc artefacts."""
    c.run("rm -rf docs/_build docs/_autosummary", pty=True)


@task(name="docs-serve")
def docs_serve(c: Context, port: int = 8001) -> None:
    """Live-rebuild docs and serve them on the given port."""
    c.run(
        f"uv run sphinx-autobuild docs docs/_build/html --port {port}",
        pty=True,
    )


# --- Local DB lifecycle ----------------------------------------------------
#
# Probe the port first; only try to start a service if nothing is already
# listening. Falls back to `brew services` for hosts (like the maintainer's
# laptop) that don't run Docker. If neither path works the task prints a
# helpful message instead of failing — running Postgres.app or a Docker
# postgres container manually is still valid.

_PORTS = {"mongo": 27017, "postgres": 5432}
_NAMES = {"mongo": "MongoDB", "postgres": "Postgres"}
_BREW_SERVICES = {
    # In preference order; first one that brew knows about wins.
    "mongo": ["mongodb-community", "mongodb-community@8.0", "mongodb-community@7.0"],
    "postgres": [
        "postgresql@17",
        "postgresql@16",
        "postgresql@15",
        "postgresql@14",
        "postgresql",
    ],
}


def _port_open(port: int, host: str = "localhost", timeout: float = 0.5) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _have_brew() -> bool:
    return shutil.which("brew") is not None


def _brew_services_table() -> dict[str, str]:
    """Map service name → status (started / stopped / error / unknown).

    Empty dict if brew isn't installed or `brew services list` fails.
    """
    if not _have_brew():
        return {}
    res = subprocess.run(["brew", "services", "list"], capture_output=True, text=True, check=False)
    if res.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in res.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 2 and not parts[0].startswith("✔"):
            out[parts[0]] = parts[1]
    return out


def _resolve_brew_service(kind: str) -> str | None:
    """Return the first brew service for ``kind`` that brew knows about."""
    table = _brew_services_table()
    for cand in _BREW_SERVICES[kind]:
        if cand in table:
            return cand
    return None


def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.5)
    return False


def _ok(msg: str) -> None:
    print(f"\033[32m✔\033[0m {msg}")


def _warn(msg: str) -> None:
    print(f"\033[33m!\033[0m {msg}")


def _info(msg: str) -> None:
    print(f"  {msg}")


@task(name="db-status")
def db_status(c: Context, mongo: bool = True, postgres: bool = True) -> None:
    """Report whether local mongo + postgres are reachable.

    Probes each port and (if brew is available) reports which brew service
    manages it. Useful before running ``inv test-mongo`` or
    ``inv test-postgres`` to diagnose connection failures.
    """
    table = _brew_services_table()
    targets: list[str] = []
    if mongo:
        targets.append("mongo")
    if postgres:
        targets.append("postgres")
    for kind in targets:
        port = _PORTS[kind]
        name = _NAMES[kind]
        listening = _port_open(port)
        brew_svc = _resolve_brew_service(kind)
        brew_state = table.get(brew_svc, "unknown") if brew_svc else "n/a"
        if listening:
            _ok(f"{name}: listening on :{port} (brew: {brew_svc or 'n/a'} = {brew_state})")
        else:
            _warn(f"{name}: NOT listening on :{port} (brew: {brew_svc or 'n/a'} = {brew_state})")


@task(name="db-up")
def db_up(c: Context, mongo: bool = True, postgres: bool = True) -> None:
    """Start local mongo + postgres if they aren't already listening.

    Tries `brew services start <service>` for whatever brew knows about.
    Skips anything already up. If brew can't help (no formula installed),
    prints a hint and moves on — start it manually (Postgres.app, docker,
    etc.) and re-run.
    """
    targets: list[str] = []
    if mongo:
        targets.append("mongo")
    if postgres:
        targets.append("postgres")
    for kind in targets:
        port = _PORTS[kind]
        name = _NAMES[kind]
        if _port_open(port):
            _ok(f"{name}: already listening on :{port}; nothing to do")
            continue
        brew_svc = _resolve_brew_service(kind)
        if brew_svc is None:
            _warn(
                f"{name}: not listening on :{port} and brew has no service for it. "
                "Start it manually (Postgres.app, docker, etc.) and re-run."
            )
            continue
        _info(f"{name}: starting via `brew services start {brew_svc}`...")
        res = subprocess.run(
            ["brew", "services", "start", brew_svc],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            _warn(
                f"{name}: brew failed to start {brew_svc}: "
                f"{res.stderr.strip() or res.stdout.strip()}"
            )
            continue
        if _wait_for_port(port):
            _ok(f"{name}: listening on :{port}")
        else:
            _warn(
                f"{name}: brew reported success but :{port} didn't come up within 15s. "
                "Run `inv db-status` to recheck."
            )


@task(name="db-down")
def db_down(c: Context, mongo: bool = True, postgres: bool = True) -> None:
    """Stop the local mongo + postgres services that brew is managing.

    No-op for services running outside brew (Postgres.app, docker, manual
    launchd plists). The ``inv db-status`` output tells you which is which.
    """
    targets: list[str] = []
    if mongo:
        targets.append("mongo")
    if postgres:
        targets.append("postgres")
    table = _brew_services_table()
    for kind in targets:
        name = _NAMES[kind]
        brew_svc = _resolve_brew_service(kind)
        if brew_svc is None:
            _warn(f"{name}: brew has no service registered; can't stop. Skipping.")
            continue
        state = table.get(brew_svc, "unknown")
        if state != "started":
            _info(f"{name}: brew service {brew_svc} is {state}; nothing to stop.")
            continue
        _info(f"{name}: stopping via `brew services stop {brew_svc}`...")
        res = subprocess.run(
            ["brew", "services", "stop", brew_svc],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            _ok(f"{name}: stopped ({brew_svc})")
        else:
            _warn(
                f"{name}: brew failed to stop {brew_svc}: "
                f"{res.stderr.strip() or res.stdout.strip()}"
            )

from __future__ import annotations

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


@task
def test(c: Context, k: str = "", verbose: bool = False) -> None:
    """Run the parallel test suite."""
    flags = ["uv run python -m pytest", "-n", "auto"]
    if verbose:
        flags.append("-vv")
    if k:
        flags.extend(["-k", k])
    c.run(" ".join(flags), pty=True)


@task(name="test-serial")
def test_serial(c: Context, k: str = "") -> None:
    """Run tests serially (useful for diagnosing flakes)."""
    cmd = "uv run python -m pytest -vv"
    if k:
        cmd += f" -k {k}"
    c.run(cmd, pty=True)


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

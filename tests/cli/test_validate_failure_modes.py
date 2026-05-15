"""Failure-mode tests: deliberately break the operator's setup and
assert ``regstack validate`` surfaces the right diagnostic.

Each scenario flips ONE thing — wrong URL prefix, console body
suppressed, no log source — and confirms the expected ``CheckResult``
name appears in the failures list. The runner test
(``test_validate_runner.py``) covers the happy path; this file
covers the diagnostic-quality contract.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport

from regstack import RegStack, RegStackConfig
from regstack.cli._results import CheckResult
from regstack.cli.validate.http import HttpProbe
from regstack.cli.validate.logtail import LogTailer, parse_log_source
from regstack.cli.validate.phases import (
    core_auth as core_auth_phase,
)
from regstack.cli.validate.phases import (
    feature_discover as feature_phase,
)
from regstack.cli.validate.phases import (
    reachability as reachability_phase,
)
from regstack.cli.validate.runner import (
    RunnerContext,
    ValidationRunner,
    make_probe_identity,
)
from regstack.config.schema import EmailConfig, SmsConfig
from regstack.email.console import ConsoleEmailService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def app_factory(tmp_path: Path):
    """Yield a builder so individual tests can vary log_bodies, etc."""
    instances: list[RegStack] = []

    async def _make(
        *, log_bodies: bool = True, prefix: str = "/api/auth"
    ) -> tuple[FastAPI, RegStack]:
        cfg = RegStackConfig.load(
            toml_path=Path("/dev/null"),
            secrets_env_path=Path("/dev/null"),
            jwt_secret=secrets.token_urlsafe(64),
            database_url=f"sqlite+aiosqlite:///{tmp_path / f'rs-{secrets.token_hex(4)}.sqlite'}",
            require_verification=True,
            allow_registration=True,
            enable_password_reset=True,
            enable_account_deletion=True,
            rate_limit_disabled=True,
            email=EmailConfig(
                backend="console", log_bodies=log_bodies, from_address="test@example.com"
            ),
            sms=SmsConfig(backend="null"),
            base_url="http://testserver",  # type: ignore[arg-type]
        )
        rs = RegStack(
            config=cfg,
            email_service=ConsoleEmailService(log_bodies=log_bodies),
        )
        await rs.install_schema()
        app = FastAPI()
        app.include_router(rs.router, prefix=prefix)
        instances.append(rs)
        return app, rs

    yield _make

    for rs in instances:
        await rs.aclose()


def _attach(path: Path, *, level: int = logging.INFO) -> logging.FileHandler:
    h = logging.FileHandler(path)
    h.setLevel(level)
    h.setFormatter(logging.Formatter("%(levelname)s %(name)s:%(message)s"))
    for name in ("regstack.email.console", "regstack.sms.null"):
        lg = logging.getLogger(name)
        lg.addHandler(h)
        lg.setLevel(level)
    return h


def _detach(h: logging.FileHandler) -> None:
    for name in ("regstack.email.console", "regstack.sms.null"):
        logging.getLogger(name).removeHandler(h)
    h.close()


def _fail_names(results: list[CheckResult]) -> list[str]:
    return [r.name for r in results if not r.ok and not r.skipped]


async def _build_ctx(
    app: FastAPI, log_path: Path | None, *, prefix: str = "/api/auth"
) -> tuple[RunnerContext, LogTailer | None, HttpProbe]:
    transport = ASGITransport(app=app)
    http = HttpProbe(f"http://testserver{prefix}", transport=transport)
    tailer: LogTailer | None = None
    if log_path is not None:
        tailer = LogTailer(parse_log_source(f"file:{log_path}"))
        await tailer.start()
        await asyncio.sleep(0.2)
    ctx = RunnerContext(
        http=http,
        tailer=tailer,
        identity=make_probe_identity(email_domain="regstack-probe.example"),
        tail_timeout=1.0,
    )
    return ctx, tailer, http


async def test_wrong_url_prefix_fails_reachability(app_factory) -> None:
    app, _rs = await app_factory(prefix="/api/auth")
    # Validator points at /wrong instead of /api/auth.
    transport = ASGITransport(app=app)
    http = HttpProbe("http://testserver/wrong", transport=transport)
    ctx = RunnerContext(
        http=http,
        tailer=None,
        identity=make_probe_identity(email_domain="regstack-probe.example"),
    )
    try:
        results = await reachability_phase.run(ctx)
    finally:
        await http.close()
    failures = _fail_names(results)
    assert "reachability" in failures, failures
    assert any("404" in r.detail for r in results if r.name == "reachability")


async def test_log_bodies_false_makes_verify_fail_with_runbook_pointer(
    app_factory, tmp_path: Path
) -> None:
    # ConsoleEmailService body is at DEBUG when log_bodies=False, but the
    # handler is at INFO — so the body line never reaches our tail file.
    app, _rs = await app_factory(log_bodies=False)
    log_path = tmp_path / "stdout.log"
    log_path.write_text("")
    handler = _attach(log_path, level=logging.INFO)
    ctx, tailer, http = await _build_ctx(app, log_path)
    try:
        results_reach = await reachability_phase.run(ctx)
        results_feat = await feature_phase.run(ctx)
        results_core = await core_auth_phase.run(ctx)
    finally:
        if tailer is not None:
            await tailer.close()
        await http.close()
        _detach(handler)

    all_results = results_reach + results_feat + results_core
    failures = _fail_names(all_results)
    assert "verify" in failures, failures
    # The diagnostic must point at the runbook.
    verify_failure = next(r for r in all_results if r.name == "verify" and not r.ok)
    assert "log_bodies" in verify_failure.detail or "console" in verify_failure.detail.lower()


async def test_no_log_source_fails_verify_phase(app_factory) -> None:
    app, _rs = await app_factory()
    ctx, _, http = await _build_ctx(app, None)
    try:
        await reachability_phase.run(ctx)
        await feature_phase.run(ctx)
        core_results = await core_auth_phase.run(ctx)
    finally:
        await http.close()
    failures = _fail_names(core_results)
    # Register succeeds, but verify can't get a token without --log-source.
    assert "verify" in failures, failures


async def test_runner_cleanup_runs_on_phase_failure(app_factory, tmp_path: Path) -> None:
    """ValidationRunner must call the cleanup phase even when an earlier
    phase aborts."""
    app, _rs = await app_factory()
    log_path = tmp_path / "stdout.log"
    log_path.write_text("")
    handler = _attach(log_path)
    ctx, tailer, http = await _build_ctx(app, log_path)

    async def explode(_ctx: RunnerContext) -> list[CheckResult]:
        return [CheckResult.failed("register", "simulated explosion")]

    cleanup_ran: list[bool] = []

    async def fake_cleanup(_ctx: RunnerContext) -> list[CheckResult]:
        cleanup_ran.append(True)
        return [CheckResult.passed("cleanup", "ran in finally as expected")]

    runner = ValidationRunner(
        ctx,
        [("register", explode)],
        cleanup_phase=fake_cleanup,
    )
    try:
        results = await runner.run()
    finally:
        if tailer is not None:
            await tailer.close()
        await http.close()
        _detach(handler)
    assert cleanup_ran == [True]
    assert "cleanup" in {r.name for r in results}

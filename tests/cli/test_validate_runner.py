"""End-to-end test: validate runner against an in-process RegStack.

Wires an ASGI transport into the HTTP probe (so no real network) and a
file-handler onto the regstack loggers (so the LogTailer's `file:`
source sees console-email body lines and SMS body lines exactly as it
would in production).

This is the most important test in the suite — if it passes, the whole
validator chain (HTTP probe, regex capture, log tail, phase
orchestration, cleanup) is exercised against the real production code
paths.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
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
    account as account_phase,
)
from regstack.cli.validate.phases import (
    cleanup as cleanup_phase,
)
from regstack.cli.validate.phases import (
    core_auth as core_auth_phase,
)
from regstack.cli.validate.phases import (
    feature_discover as feature_phase,
)
from regstack.cli.validate.phases import (
    password_reset as reset_phase,
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


@pytest.fixture
def log_capture_file(tmp_path: Path) -> Path:
    return tmp_path / "regstack.stdout"


@pytest.fixture
def attach_file_handler(log_capture_file: Path):
    """Route the email + sms loggers to a file at INFO level.

    Matches what an operator would set up by routing the deployment's
    stdout to a file readable by the validator.
    """
    handler = logging.FileHandler(log_capture_file)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s:%(message)s"))
    for name in ("regstack.email.console", "regstack.sms.null"):
        lg = logging.getLogger(name)
        lg.addHandler(handler)
        lg.setLevel(logging.INFO)
    yield handler
    for name in ("regstack.email.console", "regstack.sms.null"):
        logging.getLogger(name).removeHandler(handler)
    handler.close()


@pytest_asyncio.fixture
async def app_and_rs(
    config_overrides: dict,
    tmp_path: Path,
) -> AsyncIterator[tuple[FastAPI, RegStack]]:
    cfg = RegStackConfig.load(
        toml_path=Path("/dev/null"),
        secrets_env_path=Path("/dev/null"),
        jwt_secret=secrets.token_urlsafe(64),
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'rs.sqlite'}",
        require_verification=True,
        allow_registration=True,
        enable_password_reset=True,
        enable_account_deletion=True,
        rate_limit_disabled=True,
        email=EmailConfig(backend="console", log_bodies=True, from_address="test@example.com"),
        sms=SmsConfig(backend="null"),
        base_url="http://testserver",  # type: ignore[arg-type]
        **config_overrides,
    )
    # Deliberately NOT using FrozenClock: the bulk-revoke check is
    # `iat <= tokens_invalidated_after`, and under a frozen clock every
    # post-change-password login has `iat == cutoff` and is therefore
    # rejected. In production the float-microsecond `iat` is strictly
    # greater than the cutoff so the chain works — this test exercises
    # that real-clock invariant.
    rs = RegStack(
        config=cfg,
        email_service=ConsoleEmailService(log_bodies=True),
    )
    await rs.install_schema()
    app = FastAPI()
    app.include_router(rs.router, prefix="/api/auth")
    try:
        yield app, rs
    finally:
        await rs.aclose()


@pytest.fixture
def config_overrides() -> dict:
    return {}


def _ok_names(results: list[CheckResult]) -> list[str]:
    return [r.name for r in results if r.ok and not r.skipped]


def _fail_names(results: list[CheckResult]) -> list[str]:
    return [r.name for r in results if not r.ok and not r.skipped]


async def _run_validation(app: FastAPI, log_path: Path) -> list[CheckResult]:
    transport = ASGITransport(app=app)
    http = HttpProbe("http://testserver/api/auth", verbose=False, transport=transport)
    tailer = LogTailer(parse_log_source(f"file:{log_path}"))
    await tailer.start()
    # Give tail -F a beat to attach so it doesn't miss the
    # log-handshake line.
    await asyncio.sleep(0.2)
    identity = make_probe_identity(email_domain="regstack-probe.example")
    ctx = RunnerContext(http=http, tailer=tailer, identity=identity)
    phases = [
        ("reachability", reachability_phase.run),
        ("features", feature_phase.run),
        ("register", core_auth_phase.run),
        ("account", account_phase.run),
        ("password-reset", reset_phase.run),
    ]
    runner = ValidationRunner(ctx, phases, cleanup_phase=cleanup_phase.run)
    try:
        return await runner.run()
    finally:
        await tailer.close()
        await http.close()


async def test_full_validation_against_in_process_regstack(
    attach_file_handler,
    log_capture_file: Path,
    app_and_rs: tuple[FastAPI, RegStack],
) -> None:
    app, _rs = app_and_rs
    results = await _run_validation(app, log_capture_file)

    failures = _fail_names(results)
    rendered = "\n".join(f"{r.name}: ok={r.ok} skipped={r.skipped} — {r.detail}" for r in results)
    assert failures == [], f"unexpected failures: {failures}\n\nAll:\n{rendered}"

    oks = _ok_names(results)
    # Core auth + account + reset + cleanup must all have landed.
    for required in (
        "reachability",
        "register",
        "verify",
        "login",
        "/me",
        "logout",
        "blacklist",
        "re-login",
        "patch /me",
        "change-password",
        "change-email",
        "forgot-password",
        "reset-password",
        "cleanup",
    ):
        assert required in oks, f"missing required check {required!r} in {oks}"

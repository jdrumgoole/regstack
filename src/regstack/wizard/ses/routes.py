"""FastAPI app powering the SES setup wizard window.

Same shape as :mod:`regstack.wizard.oauth_google.routes` — token-
gated SPA + JSON endpoints. AWS-touching steps (3 credentials,
4 sender identity, 5 sandbox, 6 test send) extend the basic
validate-step payload with AWS-state fields the SPA renders inline.

Endpoints (all under ``127.0.0.1:<port>``):

- ``GET  /``                       — wizard SPA (HTML).
- ``GET  /api/state``              — existing-config snapshot.
- ``POST /api/step/{n}/validate``  — per-step validation gate.
- ``POST /api/write``              — final merge into the config
  files. Re-runs full validation server-side.
- ``POST /api/done``               — signals wizard finished;
  the server's lifecycle hook tears down uvicorn.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from regstack.wizard.ses import _aws
from regstack.wizard.ses.validators import (
    NUM_STEPS,
    validate_all,
    validate_step,
)
from regstack.wizard.ses.writer import (
    CONFIG_FILE,
    SECRETS_FILE,
    detect_existing_ses,
    merge_into_config,
)

_PACKAGE = "regstack.wizard.ses"
_TEMPLATE_DIR = "templates"
_STATIC_DIR = "static"

_TOKEN_HEADER = "X-Wizard-Token"
_TOKEN_QUERY = "token"


@dataclass(slots=True)
class WizardSettings:
    target_dir: Path
    launch_token: str
    shutdown_event: threading.Event
    existing_from_address: str | None = None


def build_wizard_app(settings: WizardSettings) -> FastAPI:
    app = FastAPI(title="regstack-ses-wizard", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.env = _build_env()

    static_dir = _default_static_dir()
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(_build_page_router())
    app.include_router(_build_api_router())
    return app


# ---------------------------------------------------------------------------
# Page router
# ---------------------------------------------------------------------------


def _build_page_router() -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def wizard_page(request: Request) -> HTMLResponse:
        settings: WizardSettings = request.app.state.settings
        if request.query_params.get(_TOKEN_QUERY) != settings.launch_token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid wizard token.")
        env: Environment = request.app.state.env
        template = env.get_template("wizard.html")
        html = template.render(
            launch_token=settings.launch_token,
            num_steps=NUM_STEPS,
        )
        return HTMLResponse(html)

    return router


# ---------------------------------------------------------------------------
# API router
# ---------------------------------------------------------------------------


def _require_token(request: Request) -> None:
    settings: WizardSettings = request.app.state.settings
    supplied = request.headers.get(_TOKEN_HEADER)
    if not supplied or supplied != settings.launch_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid wizard token.")


def _build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(_require_token)])

    @router.get("/state")
    async def state(request: Request) -> dict[str, Any]:
        settings: WizardSettings = request.app.state.settings
        config_path = settings.target_dir / CONFIG_FILE
        existing = detect_existing_ses(config_path)
        return {
            "target_dir": str(settings.target_dir.resolve()),
            "existing_ses": existing,
            "from_address": settings.existing_from_address or "",
            "config_file": CONFIG_FILE,
            "secrets_file": SECRETS_FILE,
            "num_steps": NUM_STEPS,
        }

    @router.post("/step/{n}/validate")
    async def step_validate(n: int, request: Request) -> dict[str, Any]:
        if n < 0 or n >= NUM_STEPS:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown wizard step: {n}.")
        inputs = await _read_json(request)

        # Sync shape validation first; bail early on shape errors.
        sync_result = validate_step(n, inputs)
        payload = _result_payload(sync_result)
        if not sync_result.ok:
            return payload

        # AWS-touching steps add live state to the response so the SPA
        # can render the next prompt with verified-domain / sandbox /
        # send-result data.
        if n == 3:
            await _enrich_with_credentials_probe(inputs, payload)
        elif n == 4:
            await _enrich_with_identity_probe(inputs, payload)
        elif n == 5:
            await _enrich_with_sandbox_probe(inputs, payload)
        elif n == 6 and not inputs.get("skip_test_send"):
            await _enrich_with_test_send(inputs, payload)

        return payload

    @router.post("/write")
    async def write(request: Request) -> JSONResponse:
        settings: WizardSettings = request.app.state.settings
        inputs = await _read_json(request)

        full = validate_all(inputs)
        if not full.ok:
            return JSONResponse(_result_payload(full), status_code=422)

        source = str(inputs.get("credential_source", "chain"))
        write_result = merge_into_config(
            target_dir=settings.target_dir,
            ses_region=str(inputs["ses_region"]).strip(),
            from_address=str(inputs["from_address"]).strip(),
            credential_source=source,  # type: ignore[arg-type]
            ses_profile=_or_none(inputs.get("ses_profile")),
            ses_access_key_id=_or_none(inputs.get("ses_access_key_id")),
            ses_secret_access_key=_or_none(inputs.get("ses_secret_access_key")),
        )
        return JSONResponse(
            {
                "ok": True,
                "config_path": str(write_result.config_path),
                "secrets_path": str(write_result.secrets_path),
                "config_diff": write_result.config_diff,
                "secrets_diff": write_result.secrets_diff,
                "replaced_existing": write_result.replaced_existing,
            }
        )

    @router.post("/done")
    async def done(request: Request) -> dict[str, bool]:
        settings: WizardSettings = request.app.state.settings
        settings.shutdown_event.set()
        return {"ok": True}

    return router


# ---------------------------------------------------------------------------
# AWS probes (called from the validate route)
# ---------------------------------------------------------------------------


async def _enrich_with_credentials_probe(inputs: dict[str, Any], payload: dict[str, Any]) -> None:
    probe = await _aws.probe_credentials(
        region=str(inputs.get("ses_region") or "us-east-1"),
        source=str(inputs.get("credential_source", "chain")),  # type: ignore[arg-type]
        profile=_or_none(inputs.get("ses_profile")),
        access_key_id=_or_none(inputs.get("ses_access_key_id")),
        secret_access_key=_or_none(inputs.get("ses_secret_access_key")),
    )
    payload["aws"] = {
        "credential_ok": probe.ok,
        "account_id": probe.account_id,
        "arn": probe.arn,
        "error": probe.error,
    }
    if not probe.ok:
        payload["ok"] = False
        payload.setdefault("errors", []).append(
            {"field": "credential_source", "message": f"Credentials did not resolve: {probe.error}"}
        )


async def _enrich_with_identity_probe(inputs: dict[str, Any], payload: dict[str, Any]) -> None:
    probe = await _aws.probe_sender_identity(
        region=str(inputs.get("ses_region") or "us-east-1"),
        from_address=str(inputs.get("from_address", "")).strip(),
        source=str(inputs.get("credential_source", "chain")),  # type: ignore[arg-type]
        profile=_or_none(inputs.get("ses_profile")),
        access_key_id=_or_none(inputs.get("ses_access_key_id")),
        secret_access_key=_or_none(inputs.get("ses_secret_access_key")),
    )
    verified = probe.address_status == "verified" or probe.domain_status == "verified"
    payload["aws"] = {
        "address_status": probe.address_status,
        "domain_status": probe.domain_status,
        "verified": verified,
        "error": probe.error,
    }
    if not verified and probe.error is None:
        # Soft block — SES will reject the send at step 6 anyway, but
        # surfacing it here gives the operator a chance to verify the
        # domain in the AWS console before going further.
        payload["ok"] = False
        payload.setdefault("errors", []).append(
            {
                "field": "from_address",
                "message": (
                    f"Neither {inputs.get('from_address')} nor its domain is verified in SES "
                    f"in this region. Verify in the AWS console and retry."
                ),
            }
        )


async def _enrich_with_sandbox_probe(inputs: dict[str, Any], payload: dict[str, Any]) -> None:
    probe = await _aws.probe_sandbox_state(
        region=str(inputs.get("ses_region") or "us-east-1"),
        source=str(inputs.get("credential_source", "chain")),  # type: ignore[arg-type]
        profile=_or_none(inputs.get("ses_profile")),
        access_key_id=_or_none(inputs.get("ses_access_key_id")),
        secret_access_key=_or_none(inputs.get("ses_secret_access_key")),
    )
    inputs["aws_in_sandbox"] = probe.in_sandbox
    payload["aws"] = {
        "in_sandbox": probe.in_sandbox,
        "detection": probe.detection,
        "error": probe.error,
    }
    if probe.detection == "unknown":
        payload.setdefault("warnings", []).append(
            {
                "field": "_form",
                "message": (
                    "Could not determine SES sandbox state (likely an IAM policy "
                    "denying ses:GetAccount). The wizard will proceed; verify the "
                    "account is graduated out of sandbox before relying on email in prod."
                ),
            }
        )
    # If sandbox was detected and the user hasn't yet attested, block.
    if probe.in_sandbox and not inputs.get("sandbox_attested"):
        payload["ok"] = False
        payload.setdefault("errors", []).append(
            {
                "field": "sandbox_attested",
                "message": (
                    "This account is in the SES sandbox. Tick the attestation checkbox "
                    "to confirm you understand the limitations before continuing."
                ),
            }
        )


async def _enrich_with_test_send(inputs: dict[str, Any], payload: dict[str, Any]) -> None:
    to_addr = str(inputs.get("test_recipient") or inputs.get("from_address") or "").strip()
    probe = await _aws.send_test_email(
        region=str(inputs.get("ses_region") or "us-east-1"),
        from_address=str(inputs.get("from_address", "")).strip(),
        to_address=to_addr,
        source=str(inputs.get("credential_source", "chain")),  # type: ignore[arg-type]
        profile=_or_none(inputs.get("ses_profile")),
        access_key_id=_or_none(inputs.get("ses_access_key_id")),
        secret_access_key=_or_none(inputs.get("ses_secret_access_key")),
    )
    payload["aws"] = {
        "send_ok": probe.ok,
        "message_id": probe.message_id,
        "error": probe.error,
    }
    if not probe.ok:
        payload["ok"] = False
        payload.setdefault("errors", []).append(
            {"field": "test_recipient", "message": f"SES rejected the send: {probe.error}"}
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


def _result_payload(result: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": result.ok,
        "errors": [{"field": e.field, "message": e.message} for e in result.errors],
    }
    if getattr(result, "warnings", None):
        payload["warnings"] = [{"field": w.field, "message": w.message} for w in result.warnings]
    if result.jump_to is not None:
        payload["jump_to"] = result.jump_to
    return payload


def _or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip() if not isinstance(value, str) else value.strip()
    return s or None


def _build_env() -> Environment:
    template_dir = _default_template_dir()
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _default_template_dir() -> Path:
    return Path(str(resources.files(_PACKAGE).joinpath(_TEMPLATE_DIR)))


def _default_static_dir() -> Path:
    return Path(str(resources.files(_PACKAGE).joinpath(_STATIC_DIR)))


__all__ = [
    "WizardSettings",
    "build_wizard_app",
]

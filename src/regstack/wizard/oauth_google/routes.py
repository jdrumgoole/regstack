"""FastAPI app powering the OAuth setup wizard window.

The wizard's webview loads ``GET /`` (the SPA), then drives the four
JSON endpoints to validate each step and finally write the merged
config. Every API request must carry the launch token minted by the
server at startup — without it the request is refused. This stops a
hostile process on the same machine from binding to a different port
and forging requests to ours.

Endpoints (all under ``127.0.0.1:<port>``):

- ``GET  /``                       — wizard SPA (HTML).
- ``GET  /api/state``              — existing-config snapshot for the
  initial render (base URL discovery, replace-existing detection).
- ``POST /api/step/{n}/validate``  — per-step validation gate; thin
  wrapper over :func:`validators.validate_step`.
- ``POST /api/write``              — final merge into ``regstack.toml``
  + ``regstack.secrets.env``. Re-runs full validation server-side.
- ``POST /api/done``               — signals the wizard is finished;
  the server's lifecycle hook tears down uvicorn.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from regstack.wizard.oauth_google.validators import (
    NUM_STEPS,
    validate_all,
    validate_step,
)
from regstack.wizard.oauth_google.writer import (
    CONFIG_FILE,
    SECRETS_FILE,
    compute_default_redirect_uri,
    detect_existing_oauth,
    merge_into_config,
)

_PACKAGE = "regstack.wizard.oauth_google"
_TEMPLATE_DIR = "templates"
_STATIC_DIR = "static"

_TOKEN_HEADER = "X-Wizard-Token"
_TOKEN_QUERY = "token"


@dataclass(slots=True)
class WizardSettings:
    """Per-wizard-instance state injected into the FastAPI app.

    Attributes:
        target_dir: Directory the wizard reads + writes config from.
        api_prefix: Router prefix the host uses for regstack (default
            ``/api/auth``). Used to compute the suggested redirect URI.
        launch_token: Random token required on every ``/api/*`` call.
        shutdown_event: Signalled by ``POST /api/done`` so the
            uvicorn lifecycle wrapper can stop the loop.
        existing_base_url: Optional base URL pre-populated from a
            previously-loaded ``regstack.toml`` so step 2 can show it
            as the default.
    """

    target_dir: Path
    api_prefix: str
    launch_token: str
    shutdown_event: asyncio.Event
    existing_base_url: str | None = None


def build_wizard_app(settings: WizardSettings) -> FastAPI:
    """Construct the FastAPI app for one wizard run.

    The app keeps its :class:`WizardSettings` on ``app.state`` so the
    test suite can poke at them via :class:`fastapi.testclient.TestClient`
    without spinning up uvicorn.

    Args:
        settings: Per-instance configuration; see :class:`WizardSettings`.

    Returns:
        A fully wired :class:`fastapi.FastAPI` ready to serve. Caller
        is responsible for hosting it (uvicorn or :class:`TestClient`).
    """
    app = FastAPI(title="regstack-oauth-wizard", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.env = _build_env()

    static_dir = _default_static_dir()
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(_build_page_router())
    app.include_router(_build_api_router())
    return app


# ---------------------------------------------------------------------------
# Page router (token-via-querystring on the entry GET only)
# ---------------------------------------------------------------------------


def _build_page_router() -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def wizard_page(request: Request) -> HTMLResponse:
        settings: WizardSettings = request.app.state.settings
        # The webview loads /?token=… so the SPA can read the token
        # from the URL and put it in the X-Wizard-Token header on every
        # subsequent fetch. We also gate the GET itself, so an attacker
        # who guessed the port still can't even fetch the SPA without
        # also guessing the 32-byte token.
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
# API router (token-via-header)
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
        existing = detect_existing_oauth(config_path)
        base_url = settings.existing_base_url or "http://localhost:8000"
        return {
            "target_dir": str(settings.target_dir.resolve()),
            "api_prefix": settings.api_prefix,
            "existing_oauth": existing,
            "base_url": base_url,
            "redirect_uri": compute_default_redirect_uri(base_url, settings.api_prefix),
            "config_file": CONFIG_FILE,
            "secrets_file": SECRETS_FILE,
            "num_steps": NUM_STEPS,
        }

    @router.post("/step/{n}/validate")
    async def step_validate(n: int, request: Request) -> dict[str, Any]:
        if n < 0 or n >= NUM_STEPS:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown wizard step: {n}.")
        inputs = await _read_json(request)
        result = validate_step(n, inputs)
        return _result_payload(result)

    @router.post("/write")
    async def write(request: Request) -> JSONResponse:
        settings: WizardSettings = request.app.state.settings
        inputs = await _read_json(request)

        # Replay every step server-side so a URL-hacked SPA still
        # can't write a bad config.
        full = validate_all(inputs)
        if not full.ok:
            return JSONResponse(_result_payload(full), status_code=422)

        result = merge_into_config(
            target_dir=settings.target_dir,
            base_url=str(inputs.get("base_url", "")).strip(),
            api_prefix=settings.api_prefix,
            client_id=str(inputs.get("client_id", "")).strip(),
            client_secret=str(inputs.get("client_secret", "")),
            auto_link_verified_emails=bool(inputs.get("auto_link_verified_emails", False)),
            enforce_mfa_on_oauth_signin=bool(inputs.get("enforce_mfa_on_oauth_signin", False)),
            custom_redirect_uri=_or_none(inputs.get("custom_redirect_uri")),
        )
        return JSONResponse(
            {
                "ok": True,
                "config_path": str(result.config_path),
                "secrets_path": str(result.secrets_path),
                "config_diff": result.config_diff,
                "secrets_diff": result.secrets_diff,
                "replaced_existing": result.replaced_existing,
            }
        )

    @router.post("/done")
    async def done(request: Request) -> dict[str, bool]:
        settings: WizardSettings = request.app.state.settings
        settings.shutdown_event.set()
        return {"ok": True}

    return router


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
    if result.jump_to is not None:
        payload["jump_to"] = result.jump_to
    return payload


def _or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
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

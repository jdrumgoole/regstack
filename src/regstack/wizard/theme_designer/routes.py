"""FastAPI app powering the theme designer window.

Three endpoints (plus the SPA itself), all under
``127.0.0.1:<port>``:

- ``GET  /``           — designer SPA (HTML).
- ``GET  /api/state``  — defaults + the variable schema + any
  previously-saved values (round-tripped from disk).
- ``POST /api/save``   — validate the payload and write
  ``regstack-theme.css``.
- ``POST /api/done``   — signals the lifecycle wrapper to stop the
  uvicorn loop (window-close hook calls this).

Same launch-token auth as the OAuth wizard. Same lazy-import shape
so a CLI invocation that doesn't enter the GUI doesn't pay the cost.
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

from regstack.wizard.theme_designer.validators import (
    ALL_VARS,
    COLOR_VARS,
    FONT_VARS,
    RADIUS_VARS,
    validate_vars,
)
from regstack.wizard.theme_designer.writer import (
    THEME_FILE,
    load_theme,
    save_theme,
)

_PACKAGE = "regstack.wizard.theme_designer"
_TEMPLATE_DIR = "templates"
_STATIC_DIR = "static"

_TOKEN_HEADER = "X-Wizard-Token"
_TOKEN_QUERY = "token"

# The defaults the SPA shows in the "Reset" panel and uses as the
# starting form state when no regstack-theme.css exists yet. Mirror
# the bundled regstack/ui/static/css/theme.css values so what the
# operator sees in the preview matches what they get when they don't
# touch a control.
DEFAULT_LIGHT: dict[str, str] = {
    "--rs-bg": "#ffffff",
    "--rs-bg-hover": "#f3f4f6",
    "--rs-surface": "#ffffff",
    "--rs-fg": "#111827",
    "--rs-fg-muted": "#4b5563",
    "--rs-border": "#e5e7eb",
    "--rs-accent": "#2563eb",
    "--rs-accent-fg": "#ffffff",
    "--rs-accent-bg": "rgba(37, 99, 235, 0.08)",
    "--rs-danger": "#b91c1c",
    "--rs-danger-fg": "#ffffff",
    "--rs-danger-bg": "rgba(185, 28, 28, 0.08)",
    "--rs-radius": "6px",
    "--rs-font-display": '-apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", sans-serif',
    "--rs-font-body": '-apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", sans-serif',
}

DEFAULT_DARK: dict[str, str] = {
    "--rs-bg": "#0b1220",
    "--rs-bg-hover": "#14213a",
    "--rs-surface": "#111a30",
    "--rs-fg": "#e5e7eb",
    "--rs-fg-muted": "#9ca3af",
    "--rs-border": "#1f2a44",
    "--rs-accent": "#60a5fa",
    "--rs-accent-fg": "#0b1220",
    "--rs-accent-bg": "rgba(96, 165, 250, 0.12)",
    "--rs-danger": "#f87171",
    "--rs-danger-fg": "#0b1220",
    "--rs-danger-bg": "rgba(248, 113, 113, 0.12)",
}


@dataclass(slots=True)
class DesignerSettings:
    """Per-instance state injected into the FastAPI app.

    Attributes:
        target_dir: Directory the designer reads + writes the theme
            file from.
        launch_token: Random token required on every ``/api/*`` call.
        shutdown_event: Signalled by ``POST /api/done`` so the
            uvicorn lifecycle wrapper can stop the loop.
        filename: Output filename (default ``regstack-theme.css``).
    """

    target_dir: Path
    launch_token: str
    shutdown_event: asyncio.Event
    filename: str = THEME_FILE


def build_designer_app(settings: DesignerSettings) -> FastAPI:
    """Construct the FastAPI app for one designer run."""
    app = FastAPI(title="regstack-theme-designer", docs_url=None, redoc_url=None)
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
    async def designer_page(request: Request) -> HTMLResponse:
        settings: DesignerSettings = request.app.state.settings
        if request.query_params.get(_TOKEN_QUERY) != settings.launch_token:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Missing or invalid wizard token.",
            )
        env: Environment = request.app.state.env
        template = env.get_template("designer.html")
        html = template.render(launch_token=settings.launch_token)
        return HTMLResponse(html)

    return router


# ---------------------------------------------------------------------------
# API router
# ---------------------------------------------------------------------------


def _require_token(request: Request) -> None:
    settings: DesignerSettings = request.app.state.settings
    supplied = request.headers.get(_TOKEN_HEADER)
    if not supplied or supplied != settings.launch_token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing or invalid wizard token.",
        )


def _build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(_require_token)])

    @router.get("/state")
    async def state(request: Request) -> dict[str, Any]:
        settings: DesignerSettings = request.app.state.settings
        existing = load_theme(settings.target_dir, filename=settings.filename)
        return {
            "target_dir": str(settings.target_dir.resolve()),
            "filename": settings.filename,
            "schema": {
                "color_vars": list(COLOR_VARS),
                "font_vars": list(FONT_VARS),
                "radius_vars": list(RADIUS_VARS),
                "all_vars": list(ALL_VARS),
            },
            "defaults": {
                "light": DEFAULT_LIGHT,
                "dark": DEFAULT_DARK,
            },
            "existing": existing,
        }

    @router.post("/save")
    async def save(request: Request) -> JSONResponse:
        settings: DesignerSettings = request.app.state.settings
        body = await _read_json(request)
        light = body.get("light") or {}
        dark = body.get("dark") or {}
        if not isinstance(light, dict) or not isinstance(dark, dict):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Body must be {light: {...}, dark: {...}}.",
            )

        light_result = validate_vars(light, scope="light")
        dark_result = validate_vars(dark, scope="dark")
        if not light_result.ok or not dark_result.ok:
            return JSONResponse(
                {
                    "ok": False,
                    "errors": [
                        {"field": e.field, "message": e.message, "scope": "light"}
                        for e in light_result.errors
                    ]
                    + [
                        {"field": e.field, "message": e.message, "scope": "dark"}
                        for e in dark_result.errors
                    ],
                },
                status_code=422,
            )

        result = save_theme(
            settings.target_dir,
            light=light,
            dark=dark,
            filename=settings.filename,
        )
        return JSONResponse(
            {
                "ok": True,
                "target_path": str(result.target_path),
                "light_count": result.light_count,
                "dark_count": result.dark_count,
                "bytes_written": result.bytes_written,
            }
        )

    @router.post("/done")
    async def done(request: Request) -> dict[str, bool]:
        settings: DesignerSettings = request.app.state.settings
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
    "DEFAULT_DARK",
    "DEFAULT_LIGHT",
    "DesignerSettings",
    "build_designer_app",
]

"""Thin async HTTP probe client.

httpx is the obvious choice — it's already a regstack runtime dep via
its OAuth code path — so this module is a tiny wrapper that:

- holds the base URL and the current bearer token,
- centralises timeout and redirect handling so phases don't repeat it,
- supports ``-v / --verbose`` by logging every request/response.

The wrapper is async-context-manager-friendly so the runner can `async
with` it and be sure connections close on phase failure.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("regstack.cli.validate.http")


class HttpProbe:
    """Bearer-token-aware async HTTP client scoped to one base URL.

    Use :meth:`set_token` after a successful login and :meth:`clear_token`
    after logout — every subsequent request automatically adds the
    ``Authorization: Bearer …`` header (until cleared).
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        verbose: bool = False,
        verify: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # Deliberately NOT passing ``base_url=`` to httpx: httpx merges
        # base_url + relative-path requests by replacing the path
        # component, so ``base_url="http://h/api/auth" + "/login"``
        # would hit ``http://h/login`` not ``http://h/api/auth/login``.
        # We compose URLs ourselves in ``request()``.
        self._client = httpx.AsyncClient(
            timeout=timeout,
            verify=verify,
            follow_redirects=False,
            transport=transport,
        )
        self._token: str | None = None
        self._verbose = verbose

    @property
    def base_url(self) -> str:
        return self._base_url

    def set_token(self, token: str) -> None:
        self._token = token

    def clear_token(self) -> None:
        self._token = None

    def has_token(self) -> bool:
        return self._token is not None

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_redirects: bool = False,
        auth_required: bool = True,
    ) -> httpx.Response:
        hdrs: dict[str, str] = dict(headers or {})
        if auth_required and self._token is not None:
            hdrs["Authorization"] = f"Bearer {self._token}"
        url = f"{self._base_url}/{path.lstrip('/')}"
        if self._verbose:
            log.info(">>> %s %s json=%s params=%s", method, url, json, params)
        try:
            resp = await self._client.request(
                method,
                url,
                json=json,
                params=params,
                headers=hdrs,
                follow_redirects=allow_redirects,
            )
        except httpx.RequestError as exc:
            raise HttpProbeError(f"{method} {path} failed: {type(exc).__name__}: {exc}") from exc
        if self._verbose:
            preview = resp.text[:200]
            log.info("<<< %s %s -> %d %s", method, path, resp.status_code, preview)
        return resp

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", path, **kwargs)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> HttpProbe:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()


class HttpProbeError(RuntimeError):
    """Wrapping for transport-layer failures so phases can surface them
    as ``CheckResult.failed`` instead of bare tracebacks."""

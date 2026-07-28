"""Google OIDC provider.

Authorization Code with PKCE against Google's well-known endpoints.
ID-token verification uses ``pyjwt[crypto]`` against Google's JWKS
(cached by ``PyJWKClient``).

The provider is constructed once per :class:`~regstack.app.RegStack`
and reused. It holds the configured ``client_id`` / ``client_secret``
plus an :class:`httpx.AsyncClient` for token-endpoint calls.

The ``httpx``, ``cryptography`` and ``pyjwt`` imports happen at
module top level. That's fine because this module is itself imported
lazily from :mod:`regstack.app` only when the host turns
``enable_oauth`` on — but it does mean every one of them has to be
declared in the ``oauth`` extra, not just the crypto pair.
``tests/unit/test_extra_declares_its_imports.py`` enforces that.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from jwt import PyJWKClient

from regstack.oauth.base import OAuthProvider, OAuthTokens, OAuthUserInfo
from regstack.oauth.errors import OAuthIdTokenError, OAuthTokenExchangeError

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger("regstack.oauth.google")

GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

DEFAULT_SCOPES: tuple[str, ...] = ("openid", "email", "profile")

# Bound the synchronous JWKS fetch so a slow or unreachable Google JWKS
# endpoint can't pin a worker thread indefinitely. The fetch is offloaded
# to `asyncio.to_thread`, but `urllib` defaults to no timeout — under a
# sustained JWKS outage that would let cache-miss requests exhaust the
# bounded asyncio thread pool. (Security review 2026-05-22 · W-1.)
JWKS_FETCH_TIMEOUT_SECONDS = 5


class GoogleProvider(OAuthProvider):
    """OIDC provider for Google.

    Args:
        client_id: OAuth 2.0 client ID from the Google Cloud
            console.
        client_secret: OAuth 2.0 client secret. Sent on the token
            exchange. Treat as a secret.
        http: Optional pre-built async HTTP client. Pass a custom
            client to share connection pools with the host app, or
            for tests. When ``None``, a fresh client is created
            (and closed when the provider is closed).
        jwks_url: Override Google's JWKS URL. Tests inject a fake
            URL pointing at an in-process JWKS so they can mint
            verifiable ID tokens without network access. Production
            should never set this.
        issuer: Override the expected ``iss`` claim. Same reason —
            tests only.
        scopes: OAuth scopes to request. Defaults to
            ``("openid", "email", "profile")``.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient | None = None,
        jwks_url: str = GOOGLE_JWKS_URL,
        issuer: str = GOOGLE_ISSUER,
        scopes: Iterable[str] = DEFAULT_SCOPES,
    ) -> None:
        if not client_id:
            raise ValueError("GoogleProvider: client_id is required")
        if not client_secret:
            raise ValueError("GoogleProvider: client_secret is required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http
        self._owns_http = http is None
        self._issuer = issuer
        self._scopes = tuple(scopes)
        self._jwks_client = PyJWKClient(
            jwks_url, cache_keys=True, timeout=JWKS_FETCH_TIMEOUT_SECONDS
        )

    @property
    def name(self) -> str:
        return "google"

    @property
    def client_id(self) -> str:
        return self._client_id

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        nonce: str,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
            # Always show the chooser so a user with multiple Google
            # accounts can pick the right one. Without this, Google
            # silently picks the most-recently-used account, which is
            # the wrong UX for a "link a different account" flow.
            "prompt": "select_account",
            "access_type": "online",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthTokens:
        client = self._http or httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
        finally:
            if self._owns_http and client is not self._http:
                await client.aclose()
        if response.status_code != 200:
            # Keep the provider's response body at DEBUG only. It doesn't
            # carry our client secret or the auth code, but Google's error
            # bodies are verbose and there's no reason to put them in
            # production WARNING logs — the status code is the actionable
            # signal. (Security review 2026-05-22 · I-3.)
            log.debug(
                "google token exchange response body (HTTP %s): %s",
                response.status_code,
                response.text,
            )
            raise OAuthTokenExchangeError(
                f"google token exchange failed: HTTP {response.status_code}"
            )
        body: dict[str, Any] = response.json()
        try:
            return OAuthTokens(
                access_token=body["access_token"],
                id_token=body["id_token"],
                refresh_token=body.get("refresh_token"),
            )
        except KeyError as exc:
            raise OAuthTokenExchangeError(
                f"google token response missing field {exc.args[0]!r}"
            ) from exc

    async def verify_id_token(
        self,
        id_token: str,
        *,
        expected_nonce: str,
    ) -> OAuthUserInfo:
        try:
            # PyJWKClient's fetch is synchronous urllib; on a cache miss it
            # would block the event loop for the round-trip to Google's JWKS
            # endpoint. Push it to a worker thread so concurrent requests
            # aren't stalled. (Security review 2026-05-20 · I-2.)
            signing_key_obj = await asyncio.to_thread(
                self._jwks_client.get_signing_key_from_jwt, id_token
            )
            signing_key = signing_key_obj.key
        except Exception as exc:  # PyJWKClient raises a grab-bag — collapse to ours
            raise OAuthIdTokenError(f"jwks lookup failed: {exc}") from exc
        try:
            claims = pyjwt.decode(
                id_token,
                signing_key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._issuer,
                options={"require": ["sub", "iss", "aud", "exp", "iat", "nonce"]},
            )
        except pyjwt.PyJWTError as exc:
            raise OAuthIdTokenError(f"id token verification failed: {exc}") from exc
        if claims.get("nonce") != expected_nonce:
            raise OAuthIdTokenError("id token nonce mismatch")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub:
            raise OAuthIdTokenError("id token missing sub")
        email = claims.get("email")
        return OAuthUserInfo(
            subject_id=sub,
            email=email if isinstance(email, str) else None,
            email_verified=bool(claims.get("email_verified", False)),
            full_name=claims.get("name") if isinstance(claims.get("name"), str) else None,
            picture_url=claims.get("picture") if isinstance(claims.get("picture"), str) else None,
        )

    async def aclose(self) -> None:
        """Close the owned ``httpx.AsyncClient`` if the provider built one.

        No-op when the host passed an explicit ``http`` argument; the
        host owns the client's lifetime in that case.
        """
        if self._owns_http and self._http is not None:
            await self._http.aclose()

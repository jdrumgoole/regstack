"""Google OIDC provider.

Authorization Code with PKCE against Google's well-known endpoints.
ID-token verification uses ``pyjwt[crypto]`` against Google's JWKS
(cached by ``PyJWKClient``).

The provider is constructed once per :class:`~regstack.app.RegStack`
and reused. It holds the configured ``client_id`` / ``client_secret``
plus an :class:`httpx.AsyncClient` for token-endpoint calls.

The ``cryptography`` and ``pyjwt`` imports happen at module top
level — that's fine because this module is itself imported lazily
from :mod:`regstack.app` only when the host has the ``oauth`` extra
installed and turns ``enable_oauth`` on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from jwt import PyJWKClient

from regstack.oauth.base import OAuthProvider, OAuthTokens, OAuthUserInfo
from regstack.oauth.errors import OAuthIdTokenError, OAuthTokenExchangeError

if TYPE_CHECKING:
    from collections.abc import Iterable

GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

DEFAULT_SCOPES: tuple[str, ...] = ("openid", "email", "profile")


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
        self._jwks_client = PyJWKClient(jwks_url, cache_keys=True)

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
            raise OAuthTokenExchangeError(
                f"google token exchange failed: {response.status_code} {response.text}"
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
                f"google token response missing field {exc.args[0]!r}: {body!r}"
            ) from exc

    async def verify_id_token(
        self,
        id_token: str,
        *,
        expected_nonce: str,
    ) -> OAuthUserInfo:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(id_token).key
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

"""Unit tests for ``regstack.oauth.providers.google.GoogleProvider``.

The provider's two non-trivial concerns:

1. ``authorization_url`` builds a URL with the right query
   parameters.
2. ``verify_id_token`` correctly accepts well-signed tokens and
   correctly rejects: bad signature, wrong issuer, wrong audience,
   expired, missing or mismatched nonce, missing required claim.

The tests own an RSA key pair, sign their own ID tokens, and serve
the public key as a JWKS document via a tiny in-process httpx
``MockTransport``. ``PyJWKClient`` does an HTTP GET against the
``jwks_url`` we hand the provider — pointing that at the mock
transport keeps the test offline and parallel-safe.

``exchange_code`` is exercised here with an httpx ``MockTransport``
that returns canned token responses.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import (
    RSAPrivateKey,
    generate_private_key,
)

from regstack.oauth import OAuthIdTokenError, OAuthTokenExchangeError, OAuthUserInfo
from regstack.oauth.providers.google import (
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    GoogleProvider,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_key() -> RSAPrivateKey:
    """A fresh RSA key pair for the whole module.

    Module-scoped because key generation is the slowest thing in this
    file (~100 ms) and every test uses the same key.
    """
    return generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks_doc(rsa_key: RSAPrivateKey) -> dict[str, Any]:
    """JWKS document containing the test key, ready to serve."""
    public_numbers = rsa_key.public_key().public_numbers()
    n = _b64url_uint(public_numbers.n)
    e = _b64url_uint(public_numbers.e)
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "test-key-1",
                "n": n,
                "e": e,
            }
        ]
    }


@pytest.fixture
def mock_jwks_url(jwks_doc: dict[str, Any]) -> str:
    """A URL the provider can hand to ``PyJWKClient``.

    ``PyJWKClient`` uses ``urllib`` — not ``httpx`` — for its JWKS
    fetch, so the cleanest way to feed it a fake document is a
    real (but local) HTTP server. Doing that in-process would
    complicate the test; instead we patch ``urllib.request.urlopen``
    in :func:`_install_jwks_patch`.
    """
    return "https://test.invalid/jwks.json"


@pytest.fixture(autouse=True)
def _install_jwks_patch(
    jwks_doc: dict[str, Any],
    mock_jwks_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Make ``urllib.request.urlopen`` return our JWKS for the test URL.

    PyJWKClient calls ``urlopen(jwks_url, timeout=...)`` to fetch keys.
    Tests inject a stub that recognises ``mock_jwks_url`` and returns
    a canned response; any other URL falls through and raises
    (so the tests will fail loud if a network call sneaks in).
    """
    import urllib.request as _ur

    body = json.dumps(jwks_doc).encode()
    real_urlopen = _ur.urlopen

    class _FakeResp:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            pass

    def fake_urlopen(url, *args: Any, **kwargs: Any):
        target = url.full_url if hasattr(url, "full_url") else url
        if target == mock_jwks_url:
            return _FakeResp(body)
        # Any unexpected fetch is a test bug — fail loud.
        raise AssertionError(f"unexpected urlopen({target!r})")

    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)
    yield
    monkeypatch.setattr(_ur, "urlopen", real_urlopen)


def _make_provider(
    rsa_key: RSAPrivateKey,
    mock_jwks_url: str,
    *,
    client_id: str = "test-client-id.apps.googleusercontent.com",
    client_secret: str = "test-secret",
    issuer: str = "https://accounts.google.com",
    http: httpx.AsyncClient | None = None,
) -> GoogleProvider:
    return GoogleProvider(
        client_id=client_id,
        client_secret=client_secret,
        http=http,
        jwks_url=mock_jwks_url,
        issuer=issuer,
    )


def _mint_id_token(
    rsa_key: RSAPrivateKey,
    *,
    issuer: str = "https://accounts.google.com",
    audience: str = "test-client-id.apps.googleusercontent.com",
    subject: str = "1234567890",
    email: str = "alice@example.com",
    email_verified: bool = True,
    name: str | None = "Alice Example",
    picture: str | None = "https://example.com/alice.png",
    nonce: str = "test-nonce",
    exp_offset: int = 3600,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": now,
        "exp": now + exp_offset,
        "nonce": nonce,
        "email": email,
        "email_verified": email_verified,
    }
    if name is not None:
        claims["name"] = name
    if picture is not None:
        claims["picture"] = picture
    if extra_claims:
        claims.update(extra_claims)
    private_pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pyjwt.encode(
        claims,
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key-1"},
    )


def _b64url_uint(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# authorization_url
# ---------------------------------------------------------------------------


def test_authorization_url_includes_required_params(
    rsa_key: RSAPrivateKey, mock_jwks_url: str
) -> None:
    provider = _make_provider(rsa_key, mock_jwks_url)
    url = provider.authorization_url(
        redirect_uri="http://localhost:8000/api/auth/oauth/google/callback",
        state="state-123",
        code_challenge="ch-abc",
        nonce="nonce-xyz",
    )
    assert url.startswith(GOOGLE_AUTH_URL + "?")
    assert "response_type=code" in url
    assert "client_id=test-client-id.apps.googleusercontent.com" in url
    assert "code_challenge=ch-abc" in url
    assert "code_challenge_method=S256" in url
    assert "state=state-123" in url
    assert "nonce=nonce-xyz" in url
    # Scopes URL-encoded with + as space separator.
    assert "scope=openid+email+profile" in url
    # We always force the chooser so a multi-account browser shows the picker.
    assert "prompt=select_account" in url


# ---------------------------------------------------------------------------
# verify_id_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_id_token_happy_path(rsa_key: RSAPrivateKey, mock_jwks_url: str) -> None:
    provider = _make_provider(rsa_key, mock_jwks_url)
    token = _mint_id_token(rsa_key, nonce="n1")

    info = await provider.verify_id_token(token, expected_nonce="n1")

    assert isinstance(info, OAuthUserInfo)
    assert info.subject_id == "1234567890"
    assert info.email == "alice@example.com"
    assert info.email_verified is True
    assert info.full_name == "Alice Example"
    assert info.picture_url == "https://example.com/alice.png"


@pytest.mark.asyncio
async def test_verify_id_token_rejects_bad_signature(
    rsa_key: RSAPrivateKey, mock_jwks_url: str
) -> None:
    """A token signed with a *different* key must fail verification."""
    other_key = generate_private_key(public_exponent=65537, key_size=2048)
    token = _mint_id_token(other_key, nonce="n1")

    provider = _make_provider(rsa_key, mock_jwks_url)
    with pytest.raises(OAuthIdTokenError):
        await provider.verify_id_token(token, expected_nonce="n1")


@pytest.mark.asyncio
async def test_verify_id_token_rejects_wrong_issuer(
    rsa_key: RSAPrivateKey, mock_jwks_url: str
) -> None:
    token = _mint_id_token(rsa_key, issuer="https://accounts.evil.example", nonce="n1")
    provider = _make_provider(rsa_key, mock_jwks_url)
    with pytest.raises(OAuthIdTokenError):
        await provider.verify_id_token(token, expected_nonce="n1")


@pytest.mark.asyncio
async def test_verify_id_token_rejects_wrong_audience(
    rsa_key: RSAPrivateKey, mock_jwks_url: str
) -> None:
    token = _mint_id_token(rsa_key, audience="someone-elses-client-id", nonce="n1")
    provider = _make_provider(rsa_key, mock_jwks_url)
    with pytest.raises(OAuthIdTokenError):
        await provider.verify_id_token(token, expected_nonce="n1")


@pytest.mark.asyncio
async def test_verify_id_token_rejects_expired(rsa_key: RSAPrivateKey, mock_jwks_url: str) -> None:
    token = _mint_id_token(rsa_key, nonce="n1", exp_offset=-1)
    provider = _make_provider(rsa_key, mock_jwks_url)
    with pytest.raises(OAuthIdTokenError):
        await provider.verify_id_token(token, expected_nonce="n1")


@pytest.mark.asyncio
async def test_verify_id_token_rejects_nonce_mismatch(
    rsa_key: RSAPrivateKey, mock_jwks_url: str
) -> None:
    token = _mint_id_token(rsa_key, nonce="actual-nonce")
    provider = _make_provider(rsa_key, mock_jwks_url)
    with pytest.raises(OAuthIdTokenError, match="nonce"):
        await provider.verify_id_token(token, expected_nonce="something-else")


@pytest.mark.asyncio
async def test_verify_id_token_rejects_missing_required_claim(
    rsa_key: RSAPrivateKey, mock_jwks_url: str
) -> None:
    """pyjwt's ``options.require`` rejects tokens missing required claims."""
    # Build a token with no nonce claim at all by bypassing _mint_id_token.
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "test-client-id.apps.googleusercontent.com",
        "sub": "1234567890",
        "iat": now,
        "exp": now + 3600,
        "email": "a@b",
        "email_verified": True,
    }
    pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = pyjwt.encode(claims, pem, algorithm="RS256", headers={"kid": "test-key-1"})

    provider = _make_provider(rsa_key, mock_jwks_url)
    with pytest.raises(OAuthIdTokenError):
        await provider.verify_id_token(token, expected_nonce="n1")


@pytest.mark.asyncio
async def test_verify_id_token_carries_email_verified_false(
    rsa_key: RSAPrivateKey, mock_jwks_url: str
) -> None:
    """A token with ``email_verified=false`` is *not* rejected — but the
    flag flows through so the router can refuse to auto-link.
    """
    token = _mint_id_token(rsa_key, nonce="n1", email_verified=False)
    provider = _make_provider(rsa_key, mock_jwks_url)
    info = await provider.verify_id_token(token, expected_nonce="n1")
    assert info.email_verified is False
    assert info.email == "alice@example.com"


# ---------------------------------------------------------------------------
# exchange_code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_code_happy_path(rsa_key: RSAPrivateKey, mock_jwks_url: str) -> None:
    """Provider POSTs to the token endpoint and parses the response."""
    posted: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        posted["url"] = str(request.url)
        posted["body"] = dict(_parse_form(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "access_token": "test-access-token",
                "id_token": "test-id-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = _make_provider(rsa_key, mock_jwks_url, http=client)
        tokens = await provider.exchange_code(
            code="auth-code",
            redirect_uri="http://localhost/cb",
            code_verifier="verifier-abc",
        )

    assert posted["url"] == GOOGLE_TOKEN_URL
    assert posted["body"]["code"] == "auth-code"
    assert posted["body"]["redirect_uri"] == "http://localhost/cb"
    assert posted["body"]["code_verifier"] == "verifier-abc"
    assert posted["body"]["client_id"] == "test-client-id.apps.googleusercontent.com"
    assert posted["body"]["client_secret"] == "test-secret"
    assert posted["body"]["grant_type"] == "authorization_code"

    assert tokens.access_token == "test-access-token"
    assert tokens.id_token == "test-id-token"
    assert tokens.refresh_token is None


@pytest.mark.asyncio
async def test_exchange_code_raises_on_non_200(rsa_key: RSAPrivateKey, mock_jwks_url: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = _make_provider(rsa_key, mock_jwks_url, http=client)
        with pytest.raises(OAuthTokenExchangeError, match="invalid_grant"):
            await provider.exchange_code(
                code="bad", redirect_uri="http://localhost/cb", code_verifier="v"
            )


@pytest.mark.asyncio
async def test_exchange_code_raises_on_missing_id_token(
    rsa_key: RSAPrivateKey, mock_jwks_url: str
) -> None:
    """A 200 response that doesn't contain id_token is still a failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "x"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = _make_provider(rsa_key, mock_jwks_url, http=client)
        with pytest.raises(OAuthTokenExchangeError, match="id_token"):
            await provider.exchange_code(
                code="c", redirect_uri="http://localhost/cb", code_verifier="v"
            )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_provider_rejects_empty_credentials(mock_jwks_url: str) -> None:
    with pytest.raises(ValueError, match="client_id"):
        GoogleProvider(client_id="", client_secret="x", jwks_url=mock_jwks_url)
    with pytest.raises(ValueError, match="client_secret"):
        GoogleProvider(client_id="x", client_secret="", jwks_url=mock_jwks_url)


def test_provider_name_is_google(rsa_key: RSAPrivateKey, mock_jwks_url: str) -> None:
    provider = _make_provider(rsa_key, mock_jwks_url)
    assert provider.name == "google"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_form(body: str) -> list[tuple[str, str]]:
    from urllib.parse import parse_qsl

    return parse_qsl(body, keep_blank_values=True)

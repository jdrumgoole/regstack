"""OAuth provider abstraction.

Every provider (Google in v1, GitHub / Microsoft / Apple later) is a
subclass of :class:`OAuthProvider`. The class methods correspond to
the three steps of the Authorization Code with PKCE flow:

1. :meth:`OAuthProvider.authorization_url` — what URL do we redirect
   the browser to?
2. :meth:`OAuthProvider.exchange_code` — given the ``code`` the
   provider hands back, get tokens.
3. :meth:`OAuthProvider.verify_id_token` — verify the ID token's
   signature and claims, and pull out a canonical
   :class:`OAuthUserInfo`.

The router stitches those three calls together (plus our own state /
identity bookkeeping); providers don't know about regstack's storage
layer or the FastAPI app.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    """Tokens returned by a provider's token-exchange endpoint."""

    access_token: str
    """Bearer token usable against the provider's APIs. regstack does
    not store or use this for anything other than the (rare) call to a
    separate userinfo endpoint. Most providers (including Google) put
    everything we need in the ID token."""

    id_token: str
    """Signed JWT carrying the user's identity claims. This is the
    trust anchor — its signature is verified against the provider's
    JWKS."""

    refresh_token: str | None = None
    """Optional refresh token. regstack does NOT use refresh tokens
    for anything in v1; if set, it is discarded."""


@dataclass(frozen=True, slots=True)
class OAuthUserInfo:
    """Canonical, provider-agnostic user information.

    Each provider's :meth:`OAuthProvider.verify_id_token` produces one
    of these from its native claim shape. Routers downstream see only
    this — they don't care whether it came from Google's ``sub`` or
    GitHub's ``id``.
    """

    subject_id: str
    """The provider's stable, opaque user identifier. For Google: the
    ``sub`` claim. NEVER an email address — emails can change,
    subjects don't."""

    email: str | None
    """Optional email claim. May be ``None`` for providers that don't
    always return one (some GitHub configurations)."""

    email_verified: bool
    """Whether the provider considers the email verified. Auto-linking
    only happens when this is ``True`` and ``email`` is non-``None``."""

    full_name: str | None
    """Display name. Optional."""

    picture_url: str | None
    """Avatar URL. Optional."""


class OAuthProvider(ABC):
    """Abstract base class for OAuth / OIDC providers.

    Subclasses must implement the three methods of the Authorization
    Code with PKCE flow. They're free to make synchronous network
    calls inside ``async`` methods (the dance involves a single
    ``POST`` to the token endpoint) but should not introduce any
    long-running background work.

    Subclasses are stateless once constructed — repeat calls don't
    accumulate state — so a single instance per provider per
    :class:`~regstack.app.RegStack` is the intended lifetime.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The lookup key for this provider in
        :class:`~regstack.oauth.registry.OAuthRegistry`. Lowercase
        ASCII — used in URL paths (``/oauth/{name}/start``) and in
        the ``oauth_identities.provider`` column.
        """

    @abstractmethod
    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        nonce: str,
    ) -> str:
        """Build the URL the browser should be redirected to.

        Args:
            redirect_uri: The full callback URL where the provider
                will return the user. Must be registered with the
                provider out-of-band (in Google Cloud console, etc.).
            state: Opaque CSRF token. Provider returns this verbatim
                in the callback. regstack uses it as the lookup key
                for the in-flight :class:`OAuthState` row.
            code_challenge: Base64url-encoded SHA-256 of the
                ``code_verifier`` (PKCE). The verifier itself stays
                server-side.
            nonce: Random string included in the auth request and
                returned in the ID token. Defends against ID token
                replay across separate authentication ceremonies.

        Returns:
            The URL to redirect the browser to.
        """

    @abstractmethod
    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthTokens:
        """Trade an authorization code for tokens.

        Args:
            code: The ``code`` parameter the provider sent to the
                callback.
            redirect_uri: Must exactly match the ``redirect_uri``
                used in :meth:`authorization_url` — the provider
                refuses the exchange otherwise.
            code_verifier: The PKCE pre-image whose SHA-256 was
                sent as ``code_challenge`` originally. Read from
                the server-side state row.

        Returns:
            :class:`OAuthTokens` with at least ``id_token`` and
            ``access_token``.

        Raises:
            ~regstack.oauth.errors.OAuthTokenExchangeError: On any
                non-200 response from the provider's token endpoint.
        """

    @abstractmethod
    async def verify_id_token(
        self,
        id_token: str,
        *,
        expected_nonce: str,
    ) -> OAuthUserInfo:
        """Verify the ID token's signature and claims.

        Concretely this checks: signature against the provider's
        JWKS, ``iss`` matches the provider's issuer, ``aud`` matches
        the configured client_id, ``exp`` is in the future,
        ``nonce`` matches ``expected_nonce``.

        Args:
            id_token: The ID token from :class:`OAuthTokens`.
            expected_nonce: The nonce the auth request was made with,
                stored on the state row.

        Returns:
            :class:`OAuthUserInfo` distilled from the provider's
            native claim shape.

        Raises:
            ~regstack.oauth.errors.OAuthIdTokenError: If any check
                fails. The exception message is suitable for
                logging; do NOT echo it to the end user — it could
                leak which check failed and help an attacker craft a
                better forgery.
        """

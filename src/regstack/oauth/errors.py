"""Exception hierarchy for the OAuth subsystem.

All OAuth-related failures inherit from :class:`OAuthError` so a
caller that just wants to render "OAuth signin failed" doesn't need
to know the specific reason. Specific subclasses exist for callers
that *do* care (the router uses them to choose between 4xx and 5xx
status codes).
"""

from __future__ import annotations


class OAuthError(Exception):
    """Base class for every OAuth-layer failure."""


class OAuthConfigError(OAuthError):
    """The OAuth subsystem isn't configured for the requested provider.

    Raised when a router endpoint is hit for a provider whose
    ``client_id`` / ``client_secret`` aren't set, or when
    :class:`~regstack.oauth.registry.OAuthRegistry` is asked for a
    provider name that isn't registered.
    """


class OAuthTokenExchangeError(OAuthError):
    """The provider's token endpoint refused our authorization code.

    Concretely: a non-200 response from
    ``https://oauth2.googleapis.com/token`` (or equivalent). The
    exception message carries the provider's error body for logs;
    callers should NOT surface the raw message to end users.
    """


class OAuthIdTokenError(OAuthError):
    """The ID token failed verification.

    Catch-all for: bad signature, wrong issuer, wrong audience,
    expired, nonce mismatch, missing required claim. Routers
    translate this to HTTP 400 without echoing the reason.
    """

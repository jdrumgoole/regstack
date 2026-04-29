"""OAuth provider abstraction.

The contents of this package are imported lazily from
``regstack.app`` so a base install (no ``oauth`` extra) keeps
importing. Hosts that turn ``enable_oauth`` on must install the
extra::

    uv add 'regstack[oauth]'

Public surface:

- :class:`~regstack.oauth.base.OAuthProvider` — ABC every provider
  implements.
- :class:`~regstack.oauth.base.OAuthTokens` — token-exchange result.
- :class:`~regstack.oauth.base.OAuthUserInfo` — canonical, provider-
  agnostic user-info shape.
- :class:`~regstack.oauth.errors.OAuthError` — base exception.
- :class:`~regstack.oauth.registry.OAuthRegistry` — name → provider
  lookup. v1 only registers ``"google"``.
"""

from __future__ import annotations

from regstack.oauth.base import OAuthProvider, OAuthTokens, OAuthUserInfo
from regstack.oauth.errors import (
    OAuthConfigError,
    OAuthError,
    OAuthIdTokenError,
    OAuthTokenExchangeError,
)
from regstack.oauth.registry import OAuthRegistry

__all__ = [
    "OAuthConfigError",
    "OAuthError",
    "OAuthIdTokenError",
    "OAuthProvider",
    "OAuthRegistry",
    "OAuthTokenExchangeError",
    "OAuthTokens",
    "OAuthUserInfo",
]

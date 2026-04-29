"""Registry mapping provider names to provider instances.

Routers look up providers by name (the ``provider`` segment of the
URL path: ``/oauth/google/start`` → ``"google"``) so adding a
provider is a registry entry plus a new module under
:mod:`regstack.oauth.providers`.

For v1 only Google is wired up. Hosts that want a custom provider
can build their own :class:`~regstack.oauth.base.OAuthProvider` and
register it programmatically::

    rs.oauth.register(MyProvider(...))
"""

from __future__ import annotations

from regstack.oauth.base import OAuthProvider
from regstack.oauth.errors import OAuthConfigError


class OAuthRegistry:
    """Name → :class:`OAuthProvider` lookup, scoped to one
    :class:`~regstack.app.RegStack` instance.

    Empty by default. The :class:`~regstack.app.RegStack`
    constructor will populate it from
    :class:`~regstack.config.schema.OAuthConfig` when
    ``enable_oauth`` is on (M3).
    """

    def __init__(self) -> None:
        self._providers: dict[str, OAuthProvider] = {}

    def register(self, provider: OAuthProvider) -> None:
        """Register a provider. Replaces any existing provider with
        the same name.

        Args:
            provider: An :class:`OAuthProvider` implementation. The
                lookup key is ``provider.name``.
        """
        self._providers[provider.name] = provider

    def get(self, name: str) -> OAuthProvider:
        """Look up a provider by name.

        Args:
            name: The provider's :attr:`OAuthProvider.name`
                (e.g. ``"google"``).

        Returns:
            The registered provider.

        Raises:
            ~regstack.oauth.errors.OAuthConfigError: If no provider
                is registered under that name.
        """
        try:
            return self._providers[name]
        except KeyError as exc:
            raise OAuthConfigError(
                f"OAuth provider {name!r} is not configured. "
                "Did you set the matching client_id / client_secret in OAuthConfig?"
            ) from exc

    def names(self) -> list[str]:
        """Sorted list of registered provider names. Useful for
        diagnostics (and for the SSR login template, which wants to
        know which "Sign in with X" buttons to render).
        """
        return sorted(self._providers)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._providers

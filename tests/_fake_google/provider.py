"""FakeGoogleProvider — an OAuthProvider stub for integration tests.

The real :class:`~regstack.oauth.providers.google.GoogleProvider`
makes two outbound HTTP calls per sign-in (``POST`` to the token
endpoint and ``GET`` of the JWKS document). We avoid both by
substituting a provider whose ``exchange_code`` and
``verify_id_token`` return canned data the test owns.

The test "scripts" the next callback by calling
:meth:`FakeGoogleProvider.queue` with the user info and (optionally)
explicit nonce / errors; the router then drives through the normal
state-row + identity logic exactly as it would against real Google.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlencode

from regstack.oauth.base import OAuthProvider, OAuthTokens, OAuthUserInfo
from regstack.oauth.errors import OAuthIdTokenError, OAuthTokenExchangeError


@dataclass
class FakeGoogleScript:
    """One scripted response.

    Attributes:
        user_info: What ``verify_id_token`` will return on the next
            callback. ``None`` to make the verify step raise
            :class:`OAuthIdTokenError`.
        token_exchange_fails: When True, ``exchange_code`` raises
            :class:`OAuthTokenExchangeError` instead of returning
            tokens.
        nonce_override: When set, ``verify_id_token`` will only
            succeed if the router supplies this exact nonce. Defaults
            to "match whatever was passed", since the production
            provider always validates the nonce.
    """

    user_info: OAuthUserInfo | None
    token_exchange_fails: bool = False
    nonce_override: str | None = None
    captured_codes: list[str] = field(default_factory=list)


class FakeGoogleProvider(OAuthProvider):
    """An :class:`OAuthProvider` whose responses come from a queue.

    Tests interact via :meth:`queue` (set up the next callback) and
    :meth:`last_script` (inspect what the router did).
    """

    def __init__(
        self,
        *,
        client_id: str = "test-client-id.apps.googleusercontent.com",
    ) -> None:
        self._client_id = client_id
        self._queue: list[FakeGoogleScript] = []

    @property
    def name(self) -> str:
        return "google"

    @property
    def client_id(self) -> str:
        return self._client_id

    def queue(
        self,
        *,
        user_info: OAuthUserInfo | None = None,
        token_exchange_fails: bool = False,
        nonce_override: str | None = None,
    ) -> FakeGoogleScript:
        """Enqueue the next scripted callback's response."""
        script = FakeGoogleScript(
            user_info=user_info,
            token_exchange_fails=token_exchange_fails,
            nonce_override=nonce_override,
        )
        self._queue.append(script)
        return script

    def queue_user(
        self,
        *,
        subject_id: str,
        email: str | None,
        email_verified: bool = True,
        full_name: str | None = "Alice Example",
        picture_url: str | None = None,
    ) -> FakeGoogleScript:
        """Convenience: queue a happy-path sign-in with the given user info."""
        return self.queue(
            user_info=OAuthUserInfo(
                subject_id=subject_id,
                email=email,
                email_verified=email_verified,
                full_name=full_name,
                picture_url=picture_url,
            )
        )

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        nonce: str,
    ) -> str:
        # Same shape as the real Google URL — tests inspect it to verify
        # the router built it correctly. No network call.
        return "https://accounts.google.example/auth?" + urlencode(
            {
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "nonce": nonce,
                "client_id": self._client_id,
            }
        )

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OAuthTokens:
        if not self._queue:
            raise AssertionError(
                "FakeGoogleProvider.exchange_code called with no scripted response"
            )
        script = self._queue[0]
        script.captured_codes.append(code)
        if script.token_exchange_fails:
            raise OAuthTokenExchangeError("scripted: token exchange failed")
        return OAuthTokens(
            access_token="fake-access-token",
            id_token="fake-id-token",
            refresh_token=None,
        )

    async def verify_id_token(
        self,
        id_token: str,
        *,
        expected_nonce: str,
    ) -> OAuthUserInfo:
        if not self._queue:
            raise AssertionError(
                "FakeGoogleProvider.verify_id_token called with no scripted response"
            )
        script = self._queue.pop(0)
        if script.nonce_override is not None and script.nonce_override != expected_nonce:
            raise OAuthIdTokenError("scripted: nonce override mismatch")
        if script.user_info is None:
            raise OAuthIdTokenError("scripted: id token verification failed")
        return script.user_info


__all__: Iterable[str] = ["FakeGoogleProvider", "FakeGoogleScript"]

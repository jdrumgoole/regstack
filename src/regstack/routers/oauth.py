"""OAuth router — sign-in / sign-up / link / unlink against an external provider.

Mounted by :func:`regstack.routers.build_router` when
``config.enable_oauth`` is on AND at least one provider is registered
on ``rs.oauth``.

Five endpoints (per provider; v1 ships with ``"google"``):

- ``GET  /oauth/{provider}/start`` — public; redirects to the provider.
- ``GET  /oauth/{provider}/callback`` — public; handles the redirect
  back, completes the flow, redirects to ``/account/oauth-complete``.
- ``POST /oauth/exchange`` — single-use; the SPA trades the state-id
  for the freshly-minted session JWT.
- ``POST /oauth/{provider}/link/start`` — authenticated; returns the
  authorization URL the SPA should navigate to.
- ``DELETE /oauth/{provider}/link`` — authenticated; unlinks one
  identity, refusing if it's the only auth method on the account.

The router enforces:

- Same-origin ``redirect_to`` (no open redirect).
- Server-side PKCE (``code_verifier`` never leaves the server).
- Anti-enumeration on linking conflicts (a clean 409 with no leaks).
- ``users.hashed_password is None`` paths for OAuth-only users.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets as _secrets
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from regstack.backends.protocols import OAuthIdentityAlreadyLinkedError
from regstack.models.oauth_identity import OAuthIdentity
from regstack.models.oauth_state import OAuthState
from regstack.models.user import BaseUser
from regstack.oauth.errors import OAuthError, OAuthIdTokenError, OAuthTokenExchangeError

if TYPE_CHECKING:
    from regstack.app import RegStack
    from regstack.oauth.base import OAuthProvider, OAuthUserInfo

log = logging.getLogger("regstack.oauth")


class ExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=8, max_length=128)


class ExchangeResponse(BaseModel):
    access_token: str
    redirect_to: str
    was_new_account: bool
    token_type: str = "bearer"
    expires_in: int


class LinkStartResponse(BaseModel):
    authorization_url: str


class MessageResponse(BaseModel):
    message: str


class LinkedIdentitySummary(BaseModel):
    """One identity in the ``/oauth/providers`` response."""

    provider: str
    email: str | None
    linked_at: str
    last_used_at: str | None


class ProvidersResponse(BaseModel):
    """Available providers + which ones the current user has linked.

    Drives the SSR ``/account/me`` "Connected accounts" panel and the
    ``/account/login`` "Sign in with X" buttons.
    """

    available: list[str]
    linked: list[LinkedIdentitySummary]


def build_oauth_router(rs: RegStack) -> APIRouter:
    """Build the OAuth router. Captures ``rs`` in closures so two
    :class:`~regstack.app.RegStack` instances in one process don't
    share state.
    """
    router = APIRouter(prefix="/oauth", tags=["regstack-oauth"])

    @router.get(
        "/exchange",
        include_in_schema=False,
    )
    async def _no_get_exchange() -> None:
        # The SPA exchange is POST-only. Reject GET loudly so a
        # misconfigured client can't hit it accidentally.
        raise HTTPException(status_code=status.HTTP_405_METHOD_NOT_ALLOWED)

    @router.get(
        "/providers",
        response_model=ProvidersResponse,
        summary="List configured providers and which the current user has linked",
    )
    async def providers_list(
        user: BaseUser = Depends(rs.deps.current_user()),
    ) -> ProvidersResponse:
        assert user.id is not None
        identities = await rs.oauth_identities.list_for_user(user.id)
        return ProvidersResponse(
            available=rs.oauth.names(),
            linked=[
                LinkedIdentitySummary(
                    provider=i.provider,
                    email=i.email,
                    linked_at=i.linked_at.isoformat(),
                    last_used_at=i.last_used_at.isoformat() if i.last_used_at else None,
                )
                for i in identities
            ],
        )

    @router.post(
        "/exchange",
        response_model=ExchangeResponse,
        summary="Trade an OAuth state-id for a session JWT",
    )
    async def exchange(payload: ExchangeRequest) -> ExchangeResponse:
        state = await rs.oauth_states.consume(payload.id)
        if state is None or state.result_token is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="OAuth state not found or already consumed.",
            )
        if state.expires_at < rs.clock.now():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth state has expired.",
            )
        return ExchangeResponse(
            access_token=state.result_token,
            redirect_to=state.redirect_to,
            was_new_account=False,
            expires_in=rs.config.jwt_ttl_seconds,
        )

    @router.get(
        "/{provider_name}/start",
        summary="Start an OAuth sign-in flow",
    )
    async def oauth_start(
        provider_name: str,
        redirect_to: str = Query(default="/account/me"),
    ) -> RedirectResponse:
        provider = _resolve_provider(rs, provider_name)
        validated_redirect = _validate_redirect(rs, redirect_to)
        url = await _begin_flow(
            rs,
            provider,
            mode="signin",
            redirect_to=validated_redirect,
            linking_user_id=None,
        )
        await rs.hooks.fire("oauth_signin_started", provider=provider_name, mode="signin")
        return RedirectResponse(url, status_code=status.HTTP_302_FOUND)

    @router.post(
        "/{provider_name}/link/start",
        response_model=LinkStartResponse,
        summary="Start an OAuth flow that links the provider to the current user",
    )
    async def oauth_link_start(
        provider_name: str,
        user: BaseUser = Depends(rs.deps.current_user()),
        redirect_to: str = Query(default="/account/me"),
    ) -> LinkStartResponse:
        assert user.id is not None
        provider = _resolve_provider(rs, provider_name)
        validated_redirect = _validate_redirect(rs, redirect_to)
        url = await _begin_flow(
            rs,
            provider,
            mode="link",
            redirect_to=validated_redirect,
            linking_user_id=user.id,
        )
        await rs.hooks.fire("oauth_signin_started", provider=provider_name, mode="link")
        return LinkStartResponse(authorization_url=url)

    @router.delete(
        "/{provider_name}/link",
        response_model=MessageResponse,
        summary="Unlink an OAuth provider from the current account",
    )
    async def oauth_unlink(
        provider_name: str,
        user: BaseUser = Depends(rs.deps.current_user()),
    ) -> MessageResponse:
        assert user.id is not None
        identities = await rs.oauth_identities.list_for_user(user.id)
        match = next((i for i in identities if i.provider == provider_name), None)
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That provider is not linked to your account.",
            )
        # Refuse to remove the last sign-in method.
        other_identities = len(identities) - 1
        has_password = user.hashed_password is not None
        if not has_password and other_identities == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot unlink your only sign-in method. "
                    "Set a password (use forgot-password) or link another "
                    "provider first."
                ),
            )
        await rs.oauth_identities.delete(user_id=user.id, provider=provider_name)
        await rs.hooks.fire("oauth_account_unlinked", user=user, provider=provider_name)
        return MessageResponse(message=f"Unlinked {provider_name}.")

    @router.get(
        "/{provider_name}/callback",
        summary="Provider redirects the browser here after authorization",
    )
    async def oauth_callback(
        provider_name: str,
        request: Request,
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ) -> RedirectResponse:
        ui_login = _ui_login_url(rs)

        if error:
            log.info("oauth callback error from provider %s: %s", provider_name, error)
            return _redirect_with_error(ui_login, "oauth_failed")
        if not code or not state:
            return _redirect_with_error(ui_login, "missing_code_or_state")

        try:
            provider = _resolve_provider(rs, provider_name)
        except HTTPException:
            return _redirect_with_error(ui_login, "unknown_provider")

        state_row = await rs.oauth_states.find(state)
        if state_row is None or state_row.provider != provider_name:
            return _redirect_with_error(ui_login, "bad_state")
        if state_row.expires_at < rs.clock.now():
            return _redirect_with_error(ui_login, "state_expired")

        try:
            tokens = await provider.exchange_code(
                code=code,
                redirect_uri=_callback_url(rs, provider_name),
                code_verifier=state_row.code_verifier,
            )
            user_info = await provider.verify_id_token(
                tokens.id_token, expected_nonce=state_row.nonce
            )
        except OAuthTokenExchangeError as exc:
            log.warning("oauth token exchange failed: %s", exc)
            return _redirect_with_error(ui_login, "token_exchange_failed")
        except OAuthIdTokenError as exc:
            log.warning("oauth id token verification failed: %s", exc)
            return _redirect_with_error(ui_login, "id_token_failed")
        except OAuthError as exc:
            log.warning("oauth error: %s", exc)
            return _redirect_with_error(ui_login, "oauth_failed")

        try:
            user, was_new = await _resolve_user(
                rs, provider_name=provider_name, info=user_info, state_row=state_row
            )
        except _LinkConflictError as exc:
            return _redirect_with_error(ui_login, exc.code)

        # Touch last_used_at on the identity (best-effort).
        try:
            await rs.oauth_identities.touch_last_used(
                provider=provider_name,
                subject_id=user_info.subject_id,
                when=rs.clock.now(),
            )
        except Exception:  # pragma: no cover — best-effort
            log.exception("touch_last_used failed for %s/%s", provider_name, user_info.subject_id)

        # Mint the session JWT, stash it on the state row for the
        # SPA's exchange call, and redirect.
        assert user.id is not None
        token, _payload = rs.jwt.encode(user.id)
        await rs.users.set_last_login(user.id, _payload.iat)
        await rs.oauth_states.set_result_token(state_row.id, token)

        await rs.hooks.fire(
            "oauth_signin_completed",
            user=user,
            provider=provider_name,
            mode=state_row.mode,
            was_new=was_new,
        )
        if state_row.mode == "link" and not was_new:
            await rs.hooks.fire("oauth_account_linked", user=user, provider=provider_name)

        complete_url = f"{rs.config.ui_prefix.rstrip('/')}/oauth-complete?id={state_row.id}"
        return RedirectResponse(complete_url, status_code=status.HTTP_302_FOUND)

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _LinkConflictError(Exception):
    """Internal: a callback couldn't reconcile the identity to a user.

    Carries a short error code that the redirect surfaces so the SPA
    can show a tailored message without us echoing internals.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _resolve_provider(rs: RegStack, name: str) -> OAuthProvider:
    if name not in rs.oauth:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OAuth provider {name!r} is not configured.",
        )
    return rs.oauth.get(name)


def _validate_redirect(rs: RegStack, redirect_to: str) -> str:
    """Reject anything that isn't a same-origin path or full URL."""
    parts = urlsplit(redirect_to)
    if not parts.scheme and not parts.netloc:
        # Plain path like "/account/me" — fine.
        return redirect_to or "/account/me"
    base_parts = urlsplit(str(rs.config.base_url))
    if (parts.scheme, parts.netloc) != (base_parts.scheme, base_parts.netloc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="redirect_to must be same-origin.",
        )
    return redirect_to


def _ui_login_url(rs: RegStack) -> str:
    return f"{rs.config.ui_prefix.rstrip('/')}/login"


def _callback_url(rs: RegStack, provider_name: str) -> str:
    """Build the absolute callback URL the provider redirects back to."""
    cfg = rs.config
    if cfg.oauth.google_redirect_uri is not None and provider_name == "google":
        return str(cfg.oauth.google_redirect_uri)
    base = str(cfg.base_url).rstrip("/")
    return f"{base}{cfg.api_prefix.rstrip('/')}/oauth/{provider_name}/callback"


def _redirect_with_error(ui_login_url: str, code: str) -> RedirectResponse:
    sep = "&" if "?" in ui_login_url else "?"
    return RedirectResponse(
        f"{ui_login_url}{sep}error={code}",
        status_code=status.HTTP_302_FOUND,
    )


async def _begin_flow(
    rs: RegStack,
    provider: OAuthProvider,
    *,
    mode: str,
    redirect_to: str,
    linking_user_id: str | None,
) -> str:
    """Generate PKCE / nonce / state, persist a state row, return the
    authorization URL the browser should be sent to.
    """
    code_verifier = _secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    nonce = _secrets.token_urlsafe(16)
    state_id = _secrets.token_urlsafe(32)

    from datetime import timedelta as _td

    state = OAuthState(
        id=state_id,
        provider=provider.name,
        code_verifier=code_verifier,
        nonce=nonce,
        redirect_to=redirect_to,
        mode=mode,  # type: ignore[arg-type]
        linking_user_id=linking_user_id,
        created_at=rs.clock.now(),
        expires_at=rs.clock.now() + _td(seconds=rs.config.oauth.state_ttl_seconds),
    )
    await rs.oauth_states.create(state)
    return provider.authorization_url(
        redirect_uri=_callback_url(rs, provider.name),
        state=state_id,
        code_challenge=code_challenge,
        nonce=nonce,
    )


async def _resolve_user(
    rs: RegStack,
    *,
    provider_name: str,
    info: OAuthUserInfo,
    state_row: OAuthState,
) -> tuple[BaseUser, bool]:
    """Find or create the user this OAuth login should resolve to.

    Returns ``(user, was_new_account)``. Raises ``_LinkConflictError`` with a
    short error code on ambiguous / refused linking.
    """
    # 1. Already-linked identity? Sign that user in.
    identity = await rs.oauth_identities.find_by_subject(
        provider=provider_name, subject_id=info.subject_id
    )
    if identity is not None:
        if state_row.mode == "link":
            # Linking an identity that's already on a different (or even
            # the same) account is a 409 — surface it; don't silently
            # take over.
            if (
                state_row.linking_user_id is not None
                and identity.user_id != state_row.linking_user_id
            ):
                raise _LinkConflictError("identity_in_use")
            raise _LinkConflictError("already_linked")
        user = await rs.users.get_by_id(identity.user_id)
        if user is None or not user.is_active:
            raise _LinkConflictError("user_inactive")
        return user, False

    # 2. Authenticated link flow — attach the identity to the linking user.
    if state_row.mode == "link":
        assert state_row.linking_user_id is not None
        target = await rs.users.get_by_id(state_row.linking_user_id)
        if target is None or not target.is_active or target.id is None:
            raise _LinkConflictError("user_inactive")
        try:
            await rs.oauth_identities.create(
                OAuthIdentity(
                    user_id=target.id,
                    provider=provider_name,
                    subject_id=info.subject_id,
                    email=info.email,
                    linked_at=rs.clock.now(),
                )
            )
        except OAuthIdentityAlreadyLinkedError as exc:
            raise _LinkConflictError("identity_in_use") from exc
        return target, False

    # 3. Sign-in flow with no existing identity.
    if info.email:
        existing = await rs.users.get_by_email(info.email)
    else:
        existing = None

    if existing is not None:
        if rs.config.oauth.auto_link_verified_emails and info.email_verified:
            assert existing.id is not None
            try:
                await rs.oauth_identities.create(
                    OAuthIdentity(
                        user_id=existing.id,
                        provider=provider_name,
                        subject_id=info.subject_id,
                        email=info.email,
                        linked_at=rs.clock.now(),
                    )
                )
            except OAuthIdentityAlreadyLinkedError as exc:
                raise _LinkConflictError("identity_in_use") from exc
            return existing, False
        raise _LinkConflictError("email_in_use")

    # 4. Brand-new account.
    new_user = BaseUser(
        email=info.email or _placeholder_email(provider_name, info.subject_id),
        hashed_password=None,
        full_name=info.full_name,
        is_active=True,
        is_verified=bool(info.email_verified),
    )
    new_user = await rs.users.create(new_user)
    assert new_user.id is not None
    await rs.oauth_identities.create(
        OAuthIdentity(
            user_id=new_user.id,
            provider=provider_name,
            subject_id=info.subject_id,
            email=info.email,
            linked_at=rs.clock.now(),
        )
    )
    await rs.hooks.fire("user_registered", user=new_user)
    return new_user, True


def _placeholder_email(provider_name: str, subject_id: str) -> str:
    """Last-resort email for providers that didn't return one.

    Hosts that hit this should turn on the provider scopes that
    actually return email; we don't want a regstack user without an
    email for password-reset purposes. The placeholder uses the
    `.invalid` TLD so it definitely won't deliver.
    """
    return f"{provider_name}-{subject_id}@oauth.invalid"


__all__ = ["ExchangeRequest", "ExchangeResponse", "LinkStartResponse", "build_oauth_router"]

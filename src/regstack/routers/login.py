from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from regstack.auth.jwt import TokenError
from regstack.auth.mfa import generate_mfa_code
from regstack.backends.protocols import MfaVerifyOutcome
from regstack.models.mfa_code import MfaCode
from regstack.routers._schemas import LoginRequest, TokenResponse
from regstack.sms.base import SmsMessage

if TYPE_CHECKING:
    from regstack.app import RegStack


_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password.",
)
_LOGIN_MFA_PURPOSE = "login_mfa"


class MfaPendingResponse(BaseModel):
    status: str = "mfa_required"
    mfa_pending_token: str
    expires_in: int
    delivery: str = "sms"


class MfaConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mfa_pending_token: str
    code: str = Field(min_length=4, max_length=10)


def build_login_router(rs: RegStack) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/login",
        response_model=TokenResponse | MfaPendingResponse,
        responses={
            401: {"description": "Invalid credentials"},
            403: {"description": "Account disabled or unverified"},
            429: {"description": "Too many failed attempts; account temporarily locked"},
        },
        summary="Exchange credentials for a JWT — or start the MFA second step",
    )
    async def login(payload: LoginRequest):
        decision = await rs.lockout.check(payload.email)
        if decision.locked:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": (
                        "Too many failed attempts. "
                        f"Try again in {decision.retry_after_seconds} seconds."
                    )
                },
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )

        user = await rs.users.get_by_email(payload.email)
        if user is None or user.id is None:
            await rs.lockout.record_failure(payload.email)
            raise _INVALID
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is disabled.",
            )
        if not rs.password_hasher.verify(payload.password, user.hashed_password):
            await rs.lockout.record_failure(payload.email)
            raise _INVALID
        if rs.config.require_verification and not user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email address has not been verified.",
            )

        if user.is_mfa_enabled and user.phone_number:
            return await _start_mfa_step(rs, user)

        token, payload_obj = rs.jwt.encode(user.id)
        await rs.users.set_last_login(user.id, payload_obj.iat)
        await rs.lockout.clear(user.email)
        await rs.hooks.fire("user_logged_in", user=user)
        return TokenResponse(access_token=token, expires_in=rs.config.jwt_ttl_seconds)

    @router.post(
        "/login/mfa-confirm",
        response_model=TokenResponse,
        summary="Complete an MFA-required login by submitting the SMS code",
    )
    async def mfa_confirm(payload: MfaConfirmRequest) -> TokenResponse:
        try:
            user_id = _decode_mfa_token(rs, payload.mfa_pending_token)
        except TokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA token is invalid or has expired.",
            ) from exc

        result = await rs.mfa_codes.verify(user_id=user_id, kind="login_mfa", raw_code=payload.code)
        if result.outcome is not MfaVerifyOutcome.OK:
            raise _mfa_outcome(result)

        user = await rs.users.get_by_id(user_id)
        if user is None or user.id is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token does not match an active account.",
            )

        token, payload_obj = rs.jwt.encode(user.id)
        await rs.users.set_last_login(user.id, payload_obj.iat)
        await rs.lockout.clear(user.email)
        await rs.hooks.fire("user_logged_in", user=user)
        return TokenResponse(access_token=token, expires_in=rs.config.jwt_ttl_seconds)

    return router


async def _start_mfa_step(rs: RegStack, user) -> MfaPendingResponse:
    raw_code, code_hash = generate_mfa_code(rs.config)
    ttl = rs.config.sms_code_ttl_seconds
    assert user.id is not None
    await rs.mfa_codes.put(
        MfaCode(
            user_id=user.id,
            kind="login_mfa",
            code_hash=code_hash,
            expires_at=rs.clock.now() + timedelta(seconds=ttl),
            max_attempts=rs.config.sms_code_max_attempts,
        )
    )
    body = rs.mail.sms_body(
        kind="login_mfa",
        code=raw_code,
        ttl_minutes=max(ttl // 60, 1),
    )
    await rs.sms.send(
        SmsMessage(to=user.phone_number, body=body, from_number=rs.config.sms.from_number)
    )
    pending_ttl = rs.config.mfa_pending_token_ttl_seconds
    pending_token = _encode_mfa_token(rs, user.id, pending_ttl)
    await rs.hooks.fire("mfa_login_started", user=user, code=raw_code)
    return MfaPendingResponse(mfa_pending_token=pending_token, expires_in=pending_ttl)


def _encode_mfa_token(rs: RegStack, user_id: str, ttl: int) -> str:
    import secrets as _secrets

    import jwt as pyjwt

    from regstack.config.secrets import derive_secret

    now = rs.clock.now()
    claims: dict[str, Any] = {
        "sub": user_id,
        "jti": _secrets.token_urlsafe(16),
        "iat": now.timestamp(),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "purpose": _LOGIN_MFA_PURPOSE,
    }
    if rs.config.jwt_audience is not None:
        claims["aud"] = rs.config.jwt_audience
    key = derive_secret(rs.config.jwt_secret.get_secret_value(), _LOGIN_MFA_PURPOSE)
    return pyjwt.encode(claims, key, algorithm=rs.config.jwt_algorithm)


def _decode_mfa_token(rs: RegStack, token: str) -> str:
    from datetime import datetime

    import jwt as pyjwt

    from regstack.config.secrets import derive_secret

    key = derive_secret(rs.config.jwt_secret.get_secret_value(), _LOGIN_MFA_PURPOSE)
    try:
        claims = pyjwt.decode(
            token,
            key,
            algorithms=[rs.config.jwt_algorithm],
            audience=rs.config.jwt_audience,
            options={
                "require": ["sub", "exp", "iat", "jti", "purpose"],
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except pyjwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("purpose") != _LOGIN_MFA_PURPOSE:
        raise TokenError("token purpose mismatch")
    now = rs.clock.now()
    exp = datetime.fromtimestamp(int(claims["exp"]), tz=now.tzinfo)
    if now >= exp:
        raise TokenError("Token has expired")
    return str(claims["sub"])


def _mfa_outcome(result):
    code = result.outcome
    if code is MfaVerifyOutcome.MISSING:
        detail = "No pending sign-in code — start the login flow again."
    elif code is MfaVerifyOutcome.EXPIRED:
        detail = "Sign-in code has expired. Try logging in again."
    elif code is MfaVerifyOutcome.LOCKED:
        detail = "Too many wrong attempts — start the login flow again."
    else:
        detail = (
            f"Wrong code; {result.attempts_remaining} attempts remaining."
            if result.attempts_remaining
            else "Wrong code."
        )
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

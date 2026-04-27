from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from regstack.auth.jwt import TokenError
from regstack.auth.mfa import generate_mfa_code
from regstack.db.repositories.mfa_code_repo import MfaVerifyOutcome
from regstack.models.mfa_code import MfaCode
from regstack.models.user import BaseUser, UserPublic
from regstack.routers._schemas import MessageResponse
from regstack.sms.base import SmsMessage, is_valid_e164

if TYPE_CHECKING:
    from regstack.app import RegStack


_PHONE_SETUP_PURPOSE = "phone_setup"


class PhoneStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number: str = Field(min_length=4, max_length=20)
    current_password: str


class PhoneConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pending_token: str
    code: str = Field(min_length=4, max_length=10)


class PhoneStartResponse(BaseModel):
    status: str = "code_sent"
    pending_token: str
    expires_in: int


class PhoneDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str


def build_phone_router(rs: RegStack) -> APIRouter:
    router = APIRouter(prefix="/phone", tags=["regstack-phone"])

    @router.post(
        "/start",
        response_model=PhoneStartResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Send a verification code to a new phone number",
    )
    async def start(
        payload: PhoneStartRequest,
        user: BaseUser = Depends(rs.deps.current_user()),
    ) -> PhoneStartResponse:
        if not is_valid_e164(payload.phone_number):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Phone number must be in E.164 format (e.g. +14155552671).",
            )
        if not rs.password_hasher.verify(payload.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            )

        assert user.id is not None
        raw_code, code_hash = generate_mfa_code(rs.config)
        ttl = rs.config.sms_code_ttl_seconds
        await rs.mfa_codes.put(
            MfaCode(
                user_id=user.id,
                kind="phone_setup",
                code_hash=code_hash,
                expires_at=rs.clock.now() + timedelta(seconds=ttl),
                max_attempts=rs.config.sms_code_max_attempts,
            )
        )

        body = rs.mail.sms_body(
            kind="phone_setup",
            code=raw_code,
            ttl_minutes=max(ttl // 60, 1),
        )
        await rs.sms.send(
            SmsMessage(
                to=payload.phone_number,
                body=body,
                from_number=rs.config.sms.from_number,
            )
        )

        pending_ttl = rs.config.mfa_pending_token_ttl_seconds
        token = _encode_phone_setup_token(rs, user.id, payload.phone_number, pending_ttl)
        await rs.hooks.fire(
            "phone_setup_started",
            user=user,
            phone=payload.phone_number,
            code=raw_code,
        )
        return PhoneStartResponse(pending_token=token, expires_in=pending_ttl)

    @router.post(
        "/confirm",
        response_model=UserPublic,
        summary="Confirm a phone-setup code and enable SMS 2FA",
    )
    async def confirm(payload: PhoneConfirmRequest) -> UserPublic:
        try:
            user_id, phone = _decode_phone_setup_token(rs, payload.pending_token)
        except TokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pending token is invalid or has expired.",
            ) from exc

        result = await rs.mfa_codes.verify(
            user_id=user_id, kind="phone_setup", raw_code=payload.code
        )
        if result.outcome is not MfaVerifyOutcome.OK:
            raise _outcome_to_http(result)

        user = await rs.users.get_by_id(user_id)
        if user is None or user.id is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token does not match an active account.",
            )
        await rs.users.set_phone(user.id, phone)
        await rs.users.set_mfa_enabled(user.id, is_mfa_enabled=True)
        user.phone_number = phone
        user.is_mfa_enabled = True
        await rs.hooks.fire("mfa_enabled", user=user)
        return UserPublic.from_user(user)

    @router.delete(
        "",
        response_model=MessageResponse,
        summary="Disable SMS 2FA and clear the phone number",
    )
    async def disable(
        payload: PhoneDisableRequest,
        user: BaseUser = Depends(rs.deps.current_user()),
    ) -> MessageResponse:
        if not rs.password_hasher.verify(payload.current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect.",
            )
        assert user.id is not None
        await rs.users.set_phone(user.id, None)
        await rs.users.set_mfa_enabled(user.id, is_mfa_enabled=False)
        await rs.mfa_codes.delete(user_id=user.id)
        await rs.hooks.fire("mfa_disabled", user=user)
        return MessageResponse(message="SMS 2FA disabled.")

    return router


def _encode_phone_setup_token(rs: RegStack, user_id: str, phone: str, ttl: int) -> str:
    import secrets as _secrets
    from datetime import timedelta

    import jwt as pyjwt

    from regstack.config.secrets import derive_secret

    now = rs.clock.now()
    claims = {
        "sub": user_id,
        "jti": _secrets.token_urlsafe(16),
        "iat": now.timestamp(),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "purpose": _PHONE_SETUP_PURPOSE,
        "phone": phone,
    }
    if rs.config.jwt_audience is not None:
        claims["aud"] = rs.config.jwt_audience
    key = derive_secret(rs.config.jwt_secret.get_secret_value(), _PHONE_SETUP_PURPOSE)
    return pyjwt.encode(claims, key, algorithm=rs.config.jwt_algorithm)


def _decode_phone_setup_token(rs: RegStack, token: str) -> tuple[str, str]:
    from datetime import datetime

    import jwt as pyjwt

    from regstack.config.secrets import derive_secret

    key = derive_secret(rs.config.jwt_secret.get_secret_value(), _PHONE_SETUP_PURPOSE)
    try:
        claims = pyjwt.decode(
            token,
            key,
            algorithms=[rs.config.jwt_algorithm],
            audience=rs.config.jwt_audience,
            options={
                "require": ["sub", "exp", "iat", "jti", "purpose", "phone"],
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except pyjwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("purpose") != _PHONE_SETUP_PURPOSE:
        raise TokenError("token purpose mismatch")
    now = rs.clock.now()
    exp = datetime.fromtimestamp(int(claims["exp"]), tz=now.tzinfo)
    if now >= exp:
        raise TokenError("Token has expired")
    return str(claims["sub"]), str(claims["phone"])


def _outcome_to_http(result):
    code = result.outcome
    if code is MfaVerifyOutcome.MISSING:
        detail = "No pending verification code — request a new one."
    elif code is MfaVerifyOutcome.EXPIRED:
        detail = "Verification code has expired. Request a new one."
    elif code is MfaVerifyOutcome.LOCKED:
        detail = "Too many wrong attempts — request a new code."
    else:
        detail = (
            f"Wrong code; {result.attempts_remaining} attempts remaining."
            if result.attempts_remaining
            else "Wrong code."
        )
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


__all__ = [
    "PhoneConfirmRequest",
    "PhoneDisableRequest",
    "PhoneStartRequest",
    "PhoneStartResponse",
    "build_phone_router",
]

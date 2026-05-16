"""Phone setup → confirm → MFA-required login → disable.

End state restores the user to no-MFA so the cleanup phase can delete
the account with just the password.
"""

from __future__ import annotations

from regstack.cli._results import CheckResult
from regstack.cli.validate.capture import LOGIN_MFA_CODE_RE, PHONE_SETUP_CODE_RE
from regstack.cli.validate.logtail import LogTailError, TokenNotSeenError
from regstack.cli.validate.runner import RunnerContext


async def run(ctx: RunnerContext) -> list[CheckResult]:
    if ctx.skip("sms"):
        return [CheckResult.skip("sms-2fa", "skipped via --skip sms")]
    if not ctx.features.has_sms:
        return [CheckResult.skip("sms-2fa", "router not mounted on deployment")]
    if ctx.phone_number is None:
        return [CheckResult.skip("sms-2fa", "no --phone given; SMS 2FA flow not exercised")]
    if ctx.tailer is None:
        return [CheckResult.skip("sms-2fa", "no --log-source")]

    out: list[CheckResult] = []

    # phone/start
    start = await ctx.http.post(
        "/phone/start",
        json={"phone_number": ctx.phone_number, "current_password": ctx.identity.password},
    )
    if start.status_code != 202:
        return [
            CheckResult.failed(
                "phone/start",
                f"returned {start.status_code}: {start.text[:200]}",
            )
        ]
    pending_token = start.json().get("pending_token")
    if not pending_token:
        return [CheckResult.failed("phone/start", f"no pending_token: {start.json()!r}")]
    out.append(CheckResult.passed("phone/start", "code requested"))

    try:
        setup_code = await ctx.tailer.expect_code(
            PHONE_SETUP_CODE_RE,
            timeout=ctx.tail_timeout,
            description="phone-setup SMS code",
        )
    except (TokenNotSeenError, LogTailError) as exc:
        return [*out, CheckResult.failed("phone/start:tail", str(exc))]

    confirm = await ctx.http.post(
        "/phone/confirm",
        json={"pending_token": pending_token, "code": setup_code},
        auth_required=False,
    )
    if confirm.status_code != 200:
        return [
            *out,
            CheckResult.failed(
                "phone/confirm", f"returned {confirm.status_code}: {confirm.text[:200]}"
            ),
        ]
    out.append(CheckResult.passed("phone/confirm", "phone enrolled, MFA on"))

    # Force the MFA path: logout, re-login.
    await ctx.http.post("/logout")
    ctx.http.clear_token()

    login = await ctx.http.post(
        "/login",
        json={"email": ctx.identity.email, "password": ctx.identity.password},
        auth_required=False,
    )
    if login.status_code != 200:
        return [
            *out,
            CheckResult.failed("mfa-login", f"returned {login.status_code}: {login.text[:200]}"),
        ]
    body = login.json()
    if body.get("status") != "mfa_required":
        return [*out, CheckResult.failed("mfa-login", f"expected mfa_required, got {body!r}")]
    mfa_pending = body.get("mfa_pending_token")
    out.append(CheckResult.passed("mfa-login", "login returned mfa_pending_token"))

    try:
        login_code = await ctx.tailer.expect_code(
            LOGIN_MFA_CODE_RE,
            timeout=ctx.tail_timeout,
            description="login-MFA SMS code",
        )
    except (TokenNotSeenError, LogTailError) as exc:
        return [*out, CheckResult.failed("mfa-login:tail", str(exc))]

    mfa_confirm = await ctx.http.post(
        "/login/mfa-confirm",
        json={"mfa_pending_token": mfa_pending, "code": login_code},
        auth_required=False,
    )
    if mfa_confirm.status_code != 200:
        return [
            *out,
            CheckResult.failed(
                "mfa-confirm", f"returned {mfa_confirm.status_code}: {mfa_confirm.text[:200]}"
            ),
        ]
    ctx.http.set_token(mfa_confirm.json()["access_token"])
    out.append(CheckResult.passed("mfa-confirm", "MFA login completed"))

    # Disable so cleanup just needs the password.
    disable = await ctx.http.delete("/phone", json={"current_password": ctx.identity.password})
    if disable.status_code != 200:
        out.append(
            CheckResult.failed(
                "phone:disable",
                f"returned {disable.status_code}: {disable.text[:200]}",
            )
        )
    else:
        out.append(CheckResult.passed("phone:disable", "SMS 2FA disabled"))

    return out

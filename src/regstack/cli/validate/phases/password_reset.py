"""forgot-password → reset-password → confirm session bulk-revoked → re-login.

Runs as the *currently authenticated* probe user so we can verify that
the old session is invalidated by the reset, then re-login with the
new password and continue.
"""

from __future__ import annotations

import secrets

from regstack.cli._results import CheckResult
from regstack.cli.validate.capture import VERIFICATION_URL_RE, extract_token_from_url
from regstack.cli.validate.logtail import LogTailError, TokenNotSeenError
from regstack.cli.validate.runner import RunnerContext


async def run(ctx: RunnerContext) -> list[CheckResult]:
    if ctx.skip("reset"):
        return [CheckResult.skip("password-reset", "skipped via --skip reset")]
    if not ctx.features.has_password_reset:
        return [CheckResult.skip("password-reset", "endpoint disabled on deployment")]
    if ctx.tailer is None:
        return [CheckResult.skip("password-reset", "no --log-source")]

    out: list[CheckResult] = []

    forgot_resp = await ctx.http.post(
        "/forgot-password", json={"email": ctx.identity.email}, auth_required=False
    )
    if forgot_resp.status_code != 202:
        return [
            CheckResult.failed(
                "forgot-password",
                f"returned {forgot_resp.status_code}: {forgot_resp.text[:200]}",
            )
        ]
    out.append(CheckResult.passed("forgot-password", "request accepted (202)"))

    try:
        url = await ctx.tailer.expect_url(
            VERIFICATION_URL_RE,
            must_contain=["/reset-password"],
            timeout=ctx.tail_timeout,
            description="password-reset URL",
        )
    except (TokenNotSeenError, LogTailError) as exc:
        return [*out, CheckResult.failed("reset:tail", str(exc))]

    token = extract_token_from_url(url)
    if not token:
        return [*out, CheckResult.failed("reset", f"no token= in URL {url!r}")]

    new_password = secrets.token_urlsafe(32)
    reset_resp = await ctx.http.post(
        "/reset-password",
        json={"token": token, "new_password": new_password},
        auth_required=False,
    )
    if reset_resp.status_code != 200:
        return [
            *out,
            CheckResult.failed(
                "reset-password", f"returned {reset_resp.status_code}: {reset_resp.text[:200]}"
            ),
        ]
    out.append(CheckResult.passed("reset-password", "password reset accepted"))

    # Old session should be revoked.
    if ctx.http.has_token():
        revoked = await ctx.http.get("/me")
        if revoked.status_code != 401:
            out.append(
                CheckResult.failed(
                    "reset:revokes-session",
                    f"old token still valid after reset ({revoked.status_code})",
                )
            )
        else:
            out.append(CheckResult.passed("reset:revokes-session", "old token correctly rejected"))

    ctx.http.clear_token()
    ctx.identity.password = new_password
    relogin = await ctx.http.post(
        "/login",
        json={"email": ctx.identity.email, "password": ctx.identity.password},
        auth_required=False,
    )
    if relogin.status_code != 200:
        return [
            *out,
            CheckResult.failed(
                "reset:re-login",
                f"re-login with new password returned {relogin.status_code}: {relogin.text[:200]}",
            ),
        ]
    ctx.http.set_token(relogin.json()["access_token"])
    out.append(CheckResult.passed("reset:re-login", "new password works"))
    return out

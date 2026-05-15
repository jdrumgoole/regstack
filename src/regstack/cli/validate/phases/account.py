"""PATCH /me, change-password, change-email.

This phase mutates ``ctx.identity`` as it goes — by the end the
runner's stored email and password reflect the *current* live values
so the cleanup phase can still authenticate.
"""

from __future__ import annotations

import secrets

from regstack.cli._results import CheckResult
from regstack.cli.validate.capture import VERIFICATION_URL_RE, extract_token_from_url
from regstack.cli.validate.logtail import LogTailError, TokenNotSeenError
from regstack.cli.validate.runner import RunnerContext


async def run(ctx: RunnerContext) -> list[CheckResult]:
    if ctx.skip("account"):
        return [CheckResult.skip("account", "skipped via --skip account")]

    out: list[CheckResult] = []

    # PATCH /me
    patch_resp = await ctx.http.patch("/me", json={"full_name": "Probe Updated"})
    if patch_resp.status_code != 200:
        out.append(
            CheckResult.failed(
                "patch /me",
                f"returned {patch_resp.status_code}: {patch_resp.text[:200]}",
            )
        )
    elif patch_resp.json().get("full_name") != "Probe Updated":
        out.append(CheckResult.failed("patch /me", f"full_name not echoed: {patch_resp.json()!r}"))
    else:
        out.append(CheckResult.passed("patch /me", "full_name updated"))

    # change-password
    new_password = secrets.token_urlsafe(32)
    cp_resp = await ctx.http.post(
        "/change-password",
        json={"current_password": ctx.identity.password, "new_password": new_password},
    )
    if cp_resp.status_code != 200:
        out.append(
            CheckResult.failed(
                "change-password",
                f"returned {cp_resp.status_code}: {cp_resp.text[:200]}",
            )
        )
        return out
    # change-password bulk-revokes the current session.
    revoked_check = await ctx.http.get("/me")
    if revoked_check.status_code != 401:
        out.append(
            CheckResult.failed(
                "change-password:revokes-session",
                f"old token still valid after password change ({revoked_check.status_code})",
            )
        )
    else:
        out.append(CheckResult.passed("change-password", "session bulk-revoked as expected"))

    # Log back in with the new password.
    ctx.http.clear_token()
    ctx.identity.password = new_password
    relogin = await ctx.http.post(
        "/login",
        json={"email": ctx.identity.email, "password": ctx.identity.password},
        auth_required=False,
    )
    if relogin.status_code != 200:
        out.append(
            CheckResult.failed(
                "change-password:re-login",
                f"login with new password returned {relogin.status_code}: {relogin.text[:200]}",
            )
        )
        return out
    ctx.http.set_token(relogin.json()["access_token"])
    out.append(CheckResult.passed("change-password:re-login", "new password works"))

    # change-email
    if ctx.tailer is None:
        out.append(CheckResult.skip("change-email", "no --log-source"))
        return out

    new_email = ctx.identity.email.replace("validate-", "validate-renamed-", 1)
    ce_resp = await ctx.http.post(
        "/change-email",
        json={"new_email": new_email, "current_password": ctx.identity.password},
    )
    if ce_resp.status_code != 202:
        out.append(
            CheckResult.failed(
                "change-email",
                f"request returned {ce_resp.status_code}: {ce_resp.text[:200]}",
            )
        )
        return out
    try:
        url = await ctx.tailer.expect_url(
            VERIFICATION_URL_RE,
            must_contain=["/confirm-email-change"],
            timeout=ctx.tail_timeout,
            description="email-change confirmation URL",
        )
    except (TokenNotSeenError, LogTailError) as exc:
        out.append(CheckResult.failed("change-email:tail", str(exc)))
        return out

    token = extract_token_from_url(url)
    if not token:
        out.append(CheckResult.failed("change-email", f"no token in URL {url!r}"))
        return out

    confirm_resp = await ctx.http.post(
        "/confirm-email-change", json={"token": token}, auth_required=False
    )
    if confirm_resp.status_code != 200:
        out.append(
            CheckResult.failed(
                "change-email:confirm",
                f"returned {confirm_resp.status_code}: {confirm_resp.text[:200]}",
            )
        )
        return out

    # Confirm-email also bulk-revokes. Log back in with the new email.
    ctx.http.clear_token()
    ctx.identity.email = new_email
    relogin2 = await ctx.http.post(
        "/login",
        json={"email": ctx.identity.email, "password": ctx.identity.password},
        auth_required=False,
    )
    if relogin2.status_code != 200:
        out.append(
            CheckResult.failed(
                "change-email:re-login",
                f"login with new email returned {relogin2.status_code}",
            )
        )
        return out
    ctx.http.set_token(relogin2.json()["access_token"])
    out.append(CheckResult.passed("change-email", "address updated, re-login OK"))

    return out

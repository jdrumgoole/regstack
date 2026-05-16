"""Delete the throwaway probe user.

Runs in the runner's ``finally`` block so it tries even when an earlier
phase aborted. If we don't have a token, we attempt one more login
with the latest known credentials before giving up — the probe email
ends up in the final report either way so the operator can sweep
manually.
"""

from __future__ import annotations

from regstack.cli._results import CheckResult
from regstack.cli.validate.runner import RunnerContext


async def run(ctx: RunnerContext) -> list[CheckResult]:
    out: list[CheckResult] = []

    if not ctx.features.has_account_deletion:
        out.append(
            CheckResult.failed(
                "cleanup",
                f"DELETE /account disabled on deployment; probe user {ctx.identity.email} "
                "must be removed manually",
            )
        )
        return out

    if not ctx.http.has_token():
        # Attempt one final login so we can clean up.
        relogin = await ctx.http.post(
            "/login",
            json={"email": ctx.identity.email, "password": ctx.identity.password},
            auth_required=False,
        )
        if relogin.status_code == 200 and "access_token" in relogin.json():
            ctx.http.set_token(relogin.json()["access_token"])
        else:
            out.append(
                CheckResult.failed(
                    "cleanup",
                    f"cannot re-authenticate probe user {ctx.identity.email} for cleanup "
                    f"(login → {relogin.status_code}); delete manually",
                )
            )
            return out

    resp = await ctx.http.delete("/account", json={"current_password": ctx.identity.password})
    if resp.status_code != 200:
        out.append(
            CheckResult.failed(
                "cleanup",
                f"DELETE /account returned {resp.status_code}: {resp.text[:200]}; "
                f"probe user {ctx.identity.email} may persist",
            )
        )
        return out

    # Confirm the token is now invalid.
    post_delete = await ctx.http.get("/me")
    if post_delete.status_code != 401:
        out.append(
            CheckResult.failed(
                "cleanup:token-invalid",
                f"/me with deleted user's token returned {post_delete.status_code}, expected 401",
            )
        )
    else:
        out.append(CheckResult.passed("cleanup", f"probe user {ctx.identity.email} deleted"))

    return out

"""Detect which optional routers are mounted on the live deployment.

We can't read the host's config, so we infer from response codes:

- ``/oauth/providers`` requires auth, so a 401 means the router is
  mounted; a 404 means it isn't.
- ``/admin/stats`` likewise requires admin auth (401) when mounted.
- ``/phone/start`` is mounted only when ``enable_sms_2fa=True``. An
  unauthenticated POST returns 401 when mounted, 404 when not.

Password reset and account deletion are baseline-on, but a host can
disable them; we mark a "not configured" result rather than failing.
"""

from __future__ import annotations

from regstack.cli._results import CheckResult
from regstack.cli.validate.runner import RunnerContext


async def run(ctx: RunnerContext) -> list[CheckResult]:
    out: list[CheckResult] = []

    oauth_resp = await ctx.http.get("/oauth/providers", auth_required=False)
    ctx.features.has_oauth = oauth_resp.status_code != 404
    out.append(
        CheckResult.passed(
            "feature:oauth",
            "mounted" if ctx.features.has_oauth else "not mounted",
        )
    )

    admin_resp = await ctx.http.get("/admin/stats", auth_required=False)
    ctx.features.has_admin = admin_resp.status_code != 404
    out.append(
        CheckResult.passed(
            "feature:admin",
            "mounted" if ctx.features.has_admin else "not mounted",
        )
    )

    phone_resp = await ctx.http.post(
        "/phone/start",
        json={"phone_number": "+15555550100", "current_password": "x"},
        auth_required=False,
    )
    ctx.features.has_sms = phone_resp.status_code != 404
    out.append(
        CheckResult.passed(
            "feature:sms-2fa",
            "mounted" if ctx.features.has_sms else "not mounted",
        )
    )

    forgot_resp = await ctx.http.post(
        "/forgot-password",
        json={"email": "feature-probe@regstack-probe.example"},
        auth_required=False,
    )
    ctx.features.has_password_reset = forgot_resp.status_code != 404
    out.append(
        CheckResult.passed(
            "feature:password-reset",
            "enabled" if ctx.features.has_password_reset else "disabled",
        )
    )

    return out

"""Register → verify → login → /me → logout → blacklisted-token.

The verify token comes off the LogTailer; everything else is HTTP.
After this phase the runner holds a valid access token and a verified
probe user.
"""

from __future__ import annotations

from regstack.cli._results import CheckResult
from regstack.cli.validate.capture import VERIFICATION_URL_RE, extract_token_from_url
from regstack.cli.validate.logtail import LogTailError, TokenNotSeenError
from regstack.cli.validate.runner import RunnerContext


async def run(ctx: RunnerContext) -> list[CheckResult]:
    out: list[CheckResult] = []

    resp = await ctx.http.post(
        "/register",
        json={
            "email": ctx.identity.email,
            "password": ctx.identity.password,
            "full_name": "Validate Probe",
        },
        auth_required=False,
    )
    if resp.status_code not in (201, 202):
        return [
            CheckResult.failed(
                "register", f"unexpected status {resp.status_code}: {resp.text[:200]}"
            )
        ]
    body = resp.json()
    needs_verify = isinstance(body, dict) and body.get("status") == "pending_verification"
    out.append(
        CheckResult.passed(
            "register",
            f"probe user accepted ({'verification required' if needs_verify else 'auto-verified'})",
        )
    )

    if needs_verify:
        if ctx.tailer is None:
            return [
                *out,
                CheckResult.failed(
                    "verify", "deployment requires email verification but no --log-source given"
                ),
            ]
        try:
            url = await ctx.tailer.expect_url(
                VERIFICATION_URL_RE,
                must_contain=["/verify"],
                timeout=ctx.tail_timeout,
                description="verification URL",
            )
        except TokenNotSeenError as exc:
            return [
                *out,
                CheckResult.failed(
                    "verify",
                    f'{exc} — confirm `email.backend = "console"` AND '
                    "`email.log_bodies = true` in the deployment's regstack.toml "
                    "(see `regstack validate --help`, prep steps 1-2)",
                ),
            ]
        except LogTailError as exc:
            return [*out, CheckResult.failed("verify", str(exc))]

        token = extract_token_from_url(url)
        if not token:
            return [*out, CheckResult.failed("verify", f"no token= query param in URL {url!r}")]
        verify_resp = await ctx.http.post("/verify", json={"token": token}, auth_required=False)
        if verify_resp.status_code != 200:
            return [
                *out,
                CheckResult.failed(
                    "verify",
                    f"POST /verify returned {verify_resp.status_code}: {verify_resp.text[:200]}",
                ),
            ]
        out.append(CheckResult.passed("verify", "email verified via tailed token"))
    else:
        out.append(CheckResult.skip("verify", "require_verification=False on deployment"))

    # Login
    login_resp = await ctx.http.post(
        "/login",
        json={"email": ctx.identity.email, "password": ctx.identity.password},
        auth_required=False,
    )
    if login_resp.status_code != 200:
        return [
            *out,
            CheckResult.failed(
                "login", f"login returned {login_resp.status_code}: {login_resp.text[:200]}"
            ),
        ]
    login_body = login_resp.json()
    if login_body.get("status") == "mfa_required":
        return [
            *out,
            CheckResult.failed(
                "login", "fresh probe user shouldn't be MFA-gated — leftover state?"
            ),
        ]
    token = login_body.get("access_token")
    if not token:
        return [*out, CheckResult.failed("login", f"no access_token in response: {login_body!r}")]
    ctx.http.set_token(token)
    out.append(CheckResult.passed("login", "got access token"))

    # /me
    me_resp = await ctx.http.get("/me")
    if me_resp.status_code != 200:
        return [
            *out,
            CheckResult.failed("/me", f"returned {me_resp.status_code}: {me_resp.text[:200]}"),
        ]
    me_body = me_resp.json()
    if me_body.get("email", "").lower() != ctx.identity.email.lower():
        return [
            *out,
            CheckResult.failed(
                "/me", f"echoed email {me_body.get('email')!r} != registered {ctx.identity.email!r}"
            ),
        ]
    ctx.identity.user_id = me_body.get("id")
    out.append(CheckResult.passed("/me", f"echoes registered email + id={ctx.identity.user_id}"))

    # Logout + blacklist
    logout_resp = await ctx.http.post("/logout")
    if logout_resp.status_code != 200:
        return [
            *out,
            CheckResult.failed(
                "logout", f"returned {logout_resp.status_code}: {logout_resp.text[:200]}"
            ),
        ]
    out.append(CheckResult.passed("logout", "token revoked"))

    blacklist_resp = await ctx.http.get("/me")
    if blacklist_resp.status_code != 401:
        return [
            *out,
            CheckResult.failed(
                "blacklist",
                f"/me with revoked token returned {blacklist_resp.status_code}, expected 401",
            ),
        ]
    out.append(CheckResult.passed("blacklist", "revoked token correctly rejected"))
    ctx.http.clear_token()

    # Re-login for downstream phases
    relogin = await ctx.http.post(
        "/login",
        json={"email": ctx.identity.email, "password": ctx.identity.password},
        auth_required=False,
    )
    if relogin.status_code != 200:
        return [
            *out,
            CheckResult.failed(
                "re-login", f"second login returned {relogin.status_code}: {relogin.text[:200]}"
            ),
        ]
    ctx.http.set_token(relogin.json()["access_token"])
    out.append(CheckResult.passed("re-login", "fresh token for downstream phases"))

    return out

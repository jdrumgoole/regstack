"""Verify ``GET /oauth/google/start`` returns a sensible redirect.

We deliberately do NOT complete the OAuth flow — that would need a
Google test fixture. We just check that:

- the redirect target is ``accounts.google.com``,
- ``client_id`` is present (proves the provider is configured, not
  just mounted),
- ``state`` is present (proves the state row was persisted).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from regstack.cli._results import CheckResult
from regstack.cli.validate.runner import RunnerContext


async def run(ctx: RunnerContext) -> list[CheckResult]:
    if ctx.skip("oauth"):
        return [CheckResult.skip("oauth", "skipped via --skip oauth")]
    if not ctx.features.has_oauth:
        return [CheckResult.skip("oauth", "router not mounted on deployment")]

    resp = await ctx.http.get("/oauth/google/start", auth_required=False)
    if resp.status_code != 302:
        return [
            CheckResult.failed(
                "oauth:start",
                f"expected 302 from /oauth/google/start, got {resp.status_code}",
            )
        ]
    location = resp.headers.get("location", "")
    parsed = urlparse(location)
    if "accounts.google.com" not in parsed.netloc:
        return [
            CheckResult.failed(
                "oauth:start",
                f"redirect target {parsed.netloc!r} is not accounts.google.com",
            )
        ]
    qs = parse_qs(parsed.query)
    missing = [k for k in ("client_id", "state", "redirect_uri") if k not in qs]
    if missing:
        return [
            CheckResult.failed(
                "oauth:start",
                f"redirect missing query params: {', '.join(missing)}",
            )
        ]
    return [
        CheckResult.passed(
            "oauth:start",
            f"302 to accounts.google.com with client_id + state (client_id={qs['client_id'][0][:8]}…)",
        )
    ]

"""Confirm the API base URL is reachable and looks like a regstack mount.

We can't trust ``GET /openapi.json`` to exist at our prefix (FastAPI
mounts it on the app root, not the router prefix), and we don't want to
require any single endpoint to be enabled. So the cheapest reliable
probe is ``POST /login`` with empty/garbage credentials — a regstack
mount always answers 401/422 (never 404). A 404 here means the operator
gave us the wrong ``--url``, which we surface as a failed reachability
check before anything else runs.
"""

from __future__ import annotations

from regstack.cli._results import CheckResult
from regstack.cli.validate.http import HttpProbeError
from regstack.cli.validate.runner import RunnerContext


async def run(ctx: RunnerContext) -> list[CheckResult]:
    try:
        resp = await ctx.http.post(
            "/login",
            json={"email": "reachability@probe.invalid", "password": "not-a-real-pw"},
            auth_required=False,
        )
    except HttpProbeError as exc:
        return [CheckResult.failed("reachability", f"could not reach {ctx.http.base_url}: {exc}")]

    if resp.status_code == 404:
        return [
            CheckResult.failed(
                "reachability",
                f"{ctx.http.base_url}/login returned 404 — wrong --url? "
                "expected the regstack mount prefix (e.g. https://host/api/auth)",
            )
        ]
    if resp.status_code in (401, 422, 400):
        return [
            CheckResult.passed(
                "reachability",
                f"{ctx.http.base_url} reachable (login returned {resp.status_code} as expected)",
            )
        ]
    return [
        CheckResult.failed(
            "reachability",
            f"unexpected status {resp.status_code} from /login probe; "
            f"expected 401/422 — is the --url correct?",
        )
    ]

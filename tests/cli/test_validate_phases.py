"""Unit-cover the ``regstack validate`` phase functions that the runner
and failure-mode suites don't reach: the OAuth redirect check and the
full SMS-2FA flow. Both phases talk only to ``ctx.http`` and
``ctx.tailer``, so lightweight fakes exercise every branch without a
live deployment.
"""

from __future__ import annotations

from typing import Any

import pytest

from regstack.cli.validate.logtail import TokenNotSeenError
from regstack.cli.validate.phases import oauth as oauth_phase
from regstack.cli.validate.phases import sms_mfa as sms_mfa_phase
from regstack.cli.validate.runner import FeatureFlags, ProbeIdentity, RunnerContext

pytestmark = pytest.mark.asyncio


class FakeResp:
    def __init__(
        self,
        status_code: int,
        json_body: dict | None = None,
        text: str = "",
        headers: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict:
        return self._json


class FakeHttp:
    """Returns a queued response per (METHOD, path). A list value pops in
    order so a path hit twice can return different responses."""

    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str]] = []
        self.token: str | None = None

    async def _resp(self, method: str, path: str) -> FakeResp:
        self.calls.append((method, path))
        r = self._responses[(method, path)]
        if isinstance(r, list):
            return r.pop(0)
        return r

    async def get(self, path: str, **_kw: Any) -> FakeResp:
        return await self._resp("GET", path)

    async def post(self, path: str, **_kw: Any) -> FakeResp:
        return await self._resp("POST", path)

    async def delete(self, path: str, **_kw: Any) -> FakeResp:
        return await self._resp("DELETE", path)

    def clear_token(self) -> None:
        self.token = None

    def set_token(self, token: str) -> None:
        self.token = token


class FakeTailer:
    def __init__(self, codes: list[str] | None = None, raise_exc: Exception | None = None) -> None:
        self._codes = list(codes or [])
        self._raise = raise_exc

    async def expect_code(self, _regex: Any, *, timeout: float = 0, description: str = "") -> str:
        if self._raise is not None:
            raise self._raise
        return self._codes.pop(0)


def _ctx(
    *,
    http: Any = None,
    tailer: Any = None,
    features: FeatureFlags | None = None,
    skipped: set[str] | None = None,
    phone_number: str | None = "+14155552671",
) -> RunnerContext:
    return RunnerContext(
        http=http,
        tailer=tailer,
        identity=ProbeIdentity(email="probe@example.com", password="pw-123"),
        features=features or FeatureFlags(),
        skipped=skipped or set(),
        phone_number=phone_number,
        tail_timeout=0.1,
    )


def _names_ok(results: list) -> dict[str, bool]:
    return {r.name: r.ok for r in results}


# --- oauth phase -----------------------------------------------------------


async def test_oauth_skipped_via_flag() -> None:
    results = await oauth_phase.run(_ctx(skipped={"oauth"}))
    assert results[0].skipped


async def test_oauth_skipped_when_not_mounted() -> None:
    results = await oauth_phase.run(_ctx(features=FeatureFlags(has_oauth=False)))
    assert results[0].skipped
    assert "not mounted" in results[0].detail


async def test_oauth_happy_path() -> None:
    loc = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        "?client_id=abcd1234.apps&state=xyz&redirect_uri=https%3A%2F%2Fh%2Fcb"
    )
    http = FakeHttp({("GET", "/oauth/google/start"): FakeResp(302, headers={"location": loc})})
    results = await oauth_phase.run(_ctx(http=http, features=FeatureFlags(has_oauth=True)))
    assert _names_ok(results) == {"oauth:start": True}


async def test_oauth_non_302_fails() -> None:
    http = FakeHttp({("GET", "/oauth/google/start"): FakeResp(500)})
    results = await oauth_phase.run(_ctx(http=http, features=FeatureFlags(has_oauth=True)))
    assert _names_ok(results) == {"oauth:start": False}
    assert "expected 302" in results[0].detail


async def test_oauth_wrong_redirect_target_fails() -> None:
    http = FakeHttp(
        {("GET", "/oauth/google/start"): FakeResp(302, headers={"location": "https://evil.test/x"})}
    )
    results = await oauth_phase.run(_ctx(http=http, features=FeatureFlags(has_oauth=True)))
    assert results[0].ok is False
    assert "not accounts.google.com" in results[0].detail


async def test_oauth_missing_query_params_fails() -> None:
    loc = "https://accounts.google.com/o/oauth2/v2/auth?client_id=abc"  # no state/redirect_uri
    http = FakeHttp({("GET", "/oauth/google/start"): FakeResp(302, headers={"location": loc})})
    results = await oauth_phase.run(_ctx(http=http, features=FeatureFlags(has_oauth=True)))
    assert results[0].ok is False
    assert "state" in results[0].detail and "redirect_uri" in results[0].detail


# --- sms_mfa phase: skip branches ------------------------------------------


async def test_sms_skipped_via_flag() -> None:
    results = await sms_mfa_phase.run(_ctx(skipped={"sms"}, features=FeatureFlags(has_sms=True)))
    assert results[0].skipped


async def test_sms_skipped_when_router_absent() -> None:
    results = await sms_mfa_phase.run(_ctx(features=FeatureFlags(has_sms=False)))
    assert "not mounted" in results[0].detail


async def test_sms_skipped_without_phone() -> None:
    results = await sms_mfa_phase.run(_ctx(features=FeatureFlags(has_sms=True), phone_number=None))
    assert "no --phone" in results[0].detail


async def test_sms_skipped_without_tailer() -> None:
    results = await sms_mfa_phase.run(
        _ctx(features=FeatureFlags(has_sms=True), tailer=None, http=FakeHttp({}))
    )
    # tailer is None → skip before any HTTP call.
    assert results[0].skipped and "log-source" in results[0].detail


# --- sms_mfa phase: full happy path ----------------------------------------


def _mfa_http_all_ok() -> FakeHttp:
    return FakeHttp(
        {
            ("POST", "/phone/start"): FakeResp(202, {"pending_token": "ptok"}),
            ("POST", "/phone/confirm"): FakeResp(200, {"ok": True}),
            ("POST", "/logout"): FakeResp(200),
            ("POST", "/login"): FakeResp(
                200, {"status": "mfa_required", "mfa_pending_token": "mtok"}
            ),
            ("POST", "/login/mfa-confirm"): FakeResp(200, {"access_token": "AT"}),
            ("DELETE", "/phone"): FakeResp(200, {"ok": True}),
        }
    )


async def test_sms_full_flow_passes() -> None:
    http = _mfa_http_all_ok()
    tailer = FakeTailer(codes=["111111", "222222"])
    results = await sms_mfa_phase.run(
        _ctx(http=http, tailer=tailer, features=FeatureFlags(has_sms=True))
    )
    got = _names_ok(results)
    assert got == {
        "phone/start": True,
        "phone/confirm": True,
        "mfa-login": True,
        "mfa-confirm": True,
        "phone:disable": True,
    }
    assert http.token == "AT"  # set_token called with the MFA access token


# --- sms_mfa phase: failure branches ---------------------------------------


async def test_sms_phone_start_non_202_fails() -> None:
    http = FakeHttp({("POST", "/phone/start"): FakeResp(403, text="nope")})
    results = await sms_mfa_phase.run(
        _ctx(http=http, tailer=FakeTailer(), features=FeatureFlags(has_sms=True))
    )
    assert _names_ok(results) == {"phone/start": False}


async def test_sms_phone_start_missing_pending_token_fails() -> None:
    http = FakeHttp({("POST", "/phone/start"): FakeResp(202, {})})
    results = await sms_mfa_phase.run(
        _ctx(http=http, tailer=FakeTailer(), features=FeatureFlags(has_sms=True))
    )
    assert results[-1].name == "phone/start" and results[-1].ok is False


async def test_sms_setup_code_not_seen_fails() -> None:
    http = FakeHttp({("POST", "/phone/start"): FakeResp(202, {"pending_token": "ptok"})})
    tailer = FakeTailer(raise_exc=TokenNotSeenError("no code in log"))
    results = await sms_mfa_phase.run(
        _ctx(http=http, tailer=tailer, features=FeatureFlags(has_sms=True))
    )
    assert any(r.name == "phone/start:tail" and not r.ok for r in results)


async def test_sms_confirm_non_200_fails() -> None:
    http = FakeHttp(
        {
            ("POST", "/phone/start"): FakeResp(202, {"pending_token": "ptok"}),
            ("POST", "/phone/confirm"): FakeResp(400, text="bad code"),
        }
    )
    results = await sms_mfa_phase.run(
        _ctx(http=http, tailer=FakeTailer(codes=["111111"]), features=FeatureFlags(has_sms=True))
    )
    assert any(r.name == "phone/confirm" and not r.ok for r in results)


async def test_sms_login_not_mfa_required_fails() -> None:
    http = FakeHttp(
        {
            ("POST", "/phone/start"): FakeResp(202, {"pending_token": "ptok"}),
            ("POST", "/phone/confirm"): FakeResp(200, {"ok": True}),
            ("POST", "/logout"): FakeResp(200),
            ("POST", "/login"): FakeResp(200, {"status": "ok", "access_token": "AT"}),
        }
    )
    results = await sms_mfa_phase.run(
        _ctx(http=http, tailer=FakeTailer(codes=["111111"]), features=FeatureFlags(has_sms=True))
    )
    assert any(r.name == "mfa-login" and not r.ok for r in results)


async def test_sms_disable_failure_is_reported_but_not_fatal() -> None:
    http = _mfa_http_all_ok()
    http._responses[("DELETE", "/phone")] = FakeResp(500, text="boom")
    results = await sms_mfa_phase.run(
        _ctx(
            http=http,
            tailer=FakeTailer(codes=["111111", "222222"]),
            features=FeatureFlags(has_sms=True),
        )
    )
    got = _names_ok(results)
    assert got["mfa-confirm"] is True
    assert got["phone:disable"] is False

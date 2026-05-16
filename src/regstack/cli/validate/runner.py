"""Phase orchestrator for ``regstack validate``.

A :class:`ValidationRunner` holds the mutable state every phase reads
and writes (probe user credentials, current bearer token, feature
flags discovered during the reachability phase) plus the
:class:`~regstack.cli.validate.http.HttpProbe` and
:class:`~regstack.cli.validate.logtail.LogTailer` instances.

Phases are simple async functions ``(runner) -> list[CheckResult]``.
The runner just calls them in order, accumulates results, and short-
circuits when a phase signals a hard prerequisite failed (e.g. the
probe user could not be registered).
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from regstack.cli._results import CheckResult
from regstack.cli.validate.http import HttpProbe
from regstack.cli.validate.logtail import LogTailer


@dataclass(slots=True)
class FeatureFlags:
    """What the live deployment has mounted, as discovered at runtime."""

    has_oauth: bool = False
    has_sms: bool = False
    has_admin: bool = False
    has_password_reset: bool = True
    has_account_deletion: bool = True


@dataclass(slots=True)
class ProbeIdentity:
    """The throwaway user we register, mutate, and finally delete.

    The runner rotates ``email``/``password`` as flows that change them
    succeed, so cleanup always uses the *current* credentials.
    """

    email: str
    password: str
    user_id: str | None = None


@dataclass(slots=True)
class RunnerContext:
    http: HttpProbe
    tailer: LogTailer | None
    identity: ProbeIdentity
    features: FeatureFlags = field(default_factory=FeatureFlags)
    skipped: set[str] = field(default_factory=set)
    phone_number: str | None = None
    no_cleanup: bool = False
    tail_timeout: float = 30.0
    """Per-token wait timeout passed to ``LogTailer.expect_*``. Defaults
    to 30s — long enough for production console-email round trips —
    and overridable by the test suite to shrink the timeout-failure
    path."""

    def skip(self, phase: str) -> bool:
        return phase in self.skipped


Phase = Callable[[RunnerContext], Awaitable[list[CheckResult]]]


def make_probe_identity(*, email_domain: str, password: str | None = None) -> ProbeIdentity:
    local = f"validate-{uuid.uuid4().hex[:12]}"
    return ProbeIdentity(
        email=f"{local}@{email_domain}",
        password=password or secrets.token_urlsafe(32),
    )


class ValidationRunner:
    """Sequence phases, collect results, guarantee cleanup runs."""

    def __init__(
        self,
        ctx: RunnerContext,
        phases: Sequence[tuple[str, Phase]],
        *,
        cleanup_phase: Phase | None = None,
    ) -> None:
        self._ctx = ctx
        self._phases = phases
        self._cleanup_phase = cleanup_phase
        self._results: list[CheckResult] = []

    @property
    def results(self) -> list[CheckResult]:
        return list(self._results)

    async def run(self) -> list[CheckResult]:
        try:
            for name, phase in self._phases:
                phase_results = await phase(self._ctx)
                self._results.extend(phase_results)
                if any(not r.ok for r in phase_results) and self._is_hard_phase(name):
                    self._results.append(
                        CheckResult.failed(
                            f"{name}:abort",
                            "hard-prerequisite phase failed; skipping remaining phases",
                        )
                    )
                    break
        finally:
            if self._cleanup_phase is not None and not self._ctx.no_cleanup:
                try:
                    self._results.extend(await self._cleanup_phase(self._ctx))
                except Exception as exc:
                    self._results.append(
                        CheckResult.failed(
                            "cleanup:error", f"cleanup raised: {type(exc).__name__}: {exc}"
                        )
                    )
        return self._results

    @staticmethod
    def _is_hard_phase(name: str) -> bool:
        # Reachability + register failures abort the rest — every
        # downstream phase depends on having a probe user.
        return name in {"reachability", "register"}

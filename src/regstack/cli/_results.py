from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CheckResult:
    """Outcome of one named check produced by a CLI diagnostic command.

    Shared between ``regstack doctor`` (config-side checks) and
    ``regstack validate`` (live HTTP probe).
    """

    name: str
    ok: bool
    detail: str
    skipped: bool = False

    @classmethod
    def passed(cls, name: str, detail: str) -> CheckResult:
        return cls(name=name, ok=True, detail=detail)

    @classmethod
    def failed(cls, name: str, detail: str) -> CheckResult:
        return cls(name=name, ok=False, detail=detail)

    @classmethod
    def skip(cls, name: str, detail: str) -> CheckResult:
        return cls(name=name, ok=True, detail=detail, skipped=True)

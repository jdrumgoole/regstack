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
    warn: bool = False

    @classmethod
    def passed(cls, name: str, detail: str) -> CheckResult:
        return cls(name=name, ok=True, detail=detail)

    @classmethod
    def failed(cls, name: str, detail: str) -> CheckResult:
        return cls(name=name, ok=False, detail=detail)

    @classmethod
    def skip(cls, name: str, detail: str) -> CheckResult:
        return cls(name=name, ok=True, detail=detail, skipped=True)

    @classmethod
    def warned(cls, name: str, detail: str) -> CheckResult:
        """An advisory finding: not a hard failure (``ok=True`` so it
        doesn't fail the command), but surfaced distinctly so operators
        notice it. Used for things outside regstack's control that the
        operator should act on — e.g. an out-of-date database server."""
        return cls(name=name, ok=True, detail=detail, warn=True)

"""Render :class:`CheckResult` lists as human (✔/✘/↷) or JSON."""

from __future__ import annotations

import json

import click

from regstack.cli._results import CheckResult


def render_human(results: list[CheckResult]) -> str:
    lines: list[str] = []
    for r in results:
        if r.skipped:
            symbol = click.style("↷", fg="yellow")
        elif r.ok:
            symbol = click.style("✔", fg="green")
        else:
            symbol = click.style("✘", fg="red")
        lines.append(f"{symbol} {r.name}: {r.detail}")
    return "\n".join(lines)


def render_json(results: list[CheckResult]) -> str:
    return json.dumps(
        [{"name": r.name, "ok": r.ok, "skipped": r.skipped, "detail": r.detail} for r in results],
        indent=2,
    )


def failures(results: list[CheckResult]) -> int:
    return sum(1 for r in results if not r.ok and not r.skipped)

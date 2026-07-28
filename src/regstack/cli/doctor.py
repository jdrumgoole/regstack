from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
import dns.resolver

from regstack.backends.factory import build_backend, detect_backend_kind
from regstack.cli._paths import resolve_toml_path
from regstack.cli._results import CheckResult
from regstack.cli._runtime import load_runtime_config
from regstack.config.secrets import MIN_JWT_SECRET_LENGTH
from regstack.email.factory import build_email_service

if TYPE_CHECKING:
    from regstack.config.schema import RegStackConfig


@click.command(
    name="doctor",
    help="Read-only validation of the loaded regstack configuration.",
)
@click.option(
    "--config",
    "config_path_in",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help=(
        "Path to regstack.toml (or a directory containing it). "
        "Default: search cwd / $REGSTACK_CONFIG."
    ),
)
@click.option(
    "--check-dns",
    is_flag=True,
    help="Run SPF / DKIM / MX lookups for the sender domain.",
)
@click.option(
    "--send-test-email",
    "test_recipient",
    default=None,
    help="Send a probe email to this address through the configured backend.",
)
def doctor(config_path_in: Path | None, check_dns: bool, test_recipient: str | None) -> None:
    toml_path = resolve_toml_path(config_path_in)
    results = asyncio.run(
        _run(toml_path=toml_path, check_dns=check_dns, test_recipient=test_recipient)
    )
    failed = sum(1 for r in results if not r.ok)
    warned = sum(1 for r in results if r.warn)
    for r in results:
        if r.warn:
            symbol = click.style("⚠", fg="yellow")
        elif r.ok:
            symbol = click.style("✔", fg="green")
        else:
            symbol = click.style("✘", fg="red")
        click.echo(f"{symbol} {r.name}: {r.detail}")
    if warned:
        click.echo(click.style(f"\n{warned} advisory warning(s).", fg="yellow"), err=True)
    if failed:
        click.echo(click.style(f"{failed} check(s) failed.", fg="red"), err=True)
    # Clamp to 0/1 so a shell `regstack doctor && deploy` is predictable;
    # advisory warnings (e.g. an out-of-date DB server) do NOT fail the
    # command — they're surfaced but exit 0. The failure count appears on
    # the stderr line above for operators who want it. (Review #4.)
    sys.exit(1 if failed else 0)


async def _run(
    *, toml_path: Path | None, check_dns: bool, test_recipient: str | None
) -> list[CheckResult]:
    out: list[CheckResult] = []

    config = load_runtime_config(toml_path)

    secret_value = config.jwt_secret.get_secret_value()
    if not secret_value:
        out.append(CheckResult("jwt secret", False, "missing — run `regstack init`"))
    elif len(secret_value) < MIN_JWT_SECRET_LENGTH:
        out.append(
            CheckResult(
                "jwt secret",
                False,
                f"too short ({len(secret_value)} chars; need ≥{MIN_JWT_SECRET_LENGTH})",
            )
        )
    else:
        out.append(CheckResult("jwt secret", True, f"present ({len(secret_value)} chars)"))

    out.append(await _check_backend(config))
    out.append(await _check_schema(config))
    mongo_version = await _check_mongo_server_version(config)
    if mongo_version is not None:
        out.append(mongo_version)
    out.append(_check_email_factory(config))

    if check_dns:
        out.extend(_check_dns(config))

    if test_recipient:
        out.append(await _send_test_email(config, test_recipient))

    return out


async def _check_backend(config: RegStackConfig) -> CheckResult:
    kind = detect_backend_kind(config.database_url.get_secret_value())
    backend = build_backend(config)
    try:
        await backend.ping()
        return CheckResult("backend", True, f"{kind} reachable")
    except Exception as exc:
        return CheckResult("backend", False, f"{kind} unreachable: {exc}")
    finally:
        await backend.aclose()


async def _check_schema(config: RegStackConfig) -> CheckResult:
    """Confirm the schema/indexes are installed.

    For Mongo we look for the canonical email_unique + jti_unique indexes;
    for SQL backends we ask Alembic for the current revision and assert
    it matches the bundled head — drift here means hosts have a DB at
    revision X while the package ships migrations through revision Y.
    """
    from regstack.backends.base import BackendKind

    backend = build_backend(config)
    try:
        if backend.kind is BackendKind.MONGO:
            from regstack.backends.mongo import MongoBackend

            assert isinstance(backend, MongoBackend)
            db = backend.database
            users_idx = await db[config.user_collection].index_information()
            bl_idx = await db[config.blacklist_collection].index_information()
            missing = []
            if "email_unique" not in users_idx:
                missing.append(f"{config.user_collection}.email_unique")
            if "jti_unique" not in bl_idx:
                missing.append(f"{config.blacklist_collection}.jti_unique")
            if missing:
                return CheckResult(
                    "schema", False, f"missing: {', '.join(missing)} (call install_schema)"
                )
            return CheckResult("schema", True, "core indexes present")
        # SQL backends: compare alembic_version to the bundled head.
        from regstack.backends.sql.migrations import current_async, head_revision

        url = config.database_url.get_secret_value()
        live = await current_async(url)
        head = head_revision()
        if live is None:
            return CheckResult(
                "schema",
                False,
                f"alembic_version table missing (run `regstack migrate`); bundled head is {head}",
            )
        if live != head:
            return CheckResult(
                "schema",
                False,
                f"deployed revision {live} ≠ bundled head {head} (run `regstack migrate`)",
            )
        return CheckResult("schema", True, f"at head ({head})")
    except Exception as exc:
        return CheckResult("schema", False, f"check failed: {exc}")
    finally:
        await backend.aclose()


# CVE-2025-14847 ("MongoBleed", CVSS 8.7): an unauthenticated network
# attacker can leak server memory via crafted zlib-compressed messages.
# The pymongo driver is not the vulnerable component — the server binary
# is. Patched server releases by LTS track. Keyed (major, minor) →
# minimum safe (major, minor, patch). See docs/security-reports/2026-05-20.md.
_MONGO_PATCHED_BASELINE: dict[tuple[int, int], tuple[int, int, int]] = {
    (8, 2): (8, 2, 3),
    (8, 0): (8, 0, 17),
    (7, 0): (7, 0, 28),
    (6, 0): (6, 0, 27),
    (5, 0): (5, 0, 32),
    (4, 4): (4, 4, 30),
}

# The newest LTS track we have a baseline for. A server on a track newer
# than this (e.g. 8.3+, 9.x) post-dates the advisory and is treated as safe.
_MONGO_NEWEST_KNOWN_TRACK = max(_MONGO_PATCHED_BASELINE)
# The oldest LTS track the advisory lists. Anything below it (3.6, 4.0,
# 4.2, …) is EOL and below every patched release — warn.
_MONGO_OLDEST_KNOWN_TRACK = min(_MONGO_PATCHED_BASELINE)


def _parse_mongo_version(version: str) -> tuple[int, int, int] | None:
    """Parse a MongoDB version string like ``"7.0.5"`` into ``(7, 0, 5)``.

    Tolerates a release-candidate / pre-release suffix (``"8.0.0-rc1"``)
    by splitting on the first ``-``. Returns ``None`` if the leading
    three dotted components aren't all integers.
    """
    core = version.split("-", 1)[0]
    parts = core.split(".")
    if len(parts) < 3:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _assess_mongo_server_version(version: str) -> tuple[bool, str]:
    """Decide whether ``version`` is safe against CVE-2025-14847.

    Returns ``(ok, detail)``. ``ok`` is False only when the server is on
    a known-affected LTS track *below* its patched baseline — that's the
    case worth a WARNING. Newer-than-advisory and patched servers return
    True; an unrecognised/older track returns True with a "verify
    manually" note rather than crying wolf on every odd build string.
    """
    parsed = _parse_mongo_version(version)
    if parsed is None:
        return True, f"server {version} (could not parse version; verify against CVE-2025-14847)"
    track = (parsed[0], parsed[1])
    baseline = _MONGO_PATCHED_BASELINE.get(track)
    if baseline is not None:
        if parsed < baseline:
            patched = ".".join(str(n) for n in baseline)
            return False, (
                f"server {version} is vulnerable to CVE-2025-14847 (MongoBleed); "
                f"upgrade to ≥ {patched}, or disable zlib compression in mongod.conf"
            )
        return True, f"server {version} (≥ {'.'.join(str(n) for n in baseline)}, patched)"
    if track > _MONGO_NEWEST_KNOWN_TRACK:
        return True, f"server {version} (newer than CVE-2025-14847 advisory tracks)"
    if track < _MONGO_OLDEST_KNOWN_TRACK:
        return False, (
            f"server {version} is end-of-life and below every CVE-2025-14847 "
            "patched release; upgrade to a supported, patched MongoDB version"
        )
    return True, (
        f"server {version} on an unrecognised release track; verify against CVE-2025-14847 manually"
    )


async def _check_mongo_server_version(config: RegStackConfig) -> CheckResult | None:
    """Advisory check: warn if the MongoDB *server* is below the
    CVE-2025-14847 patched baseline. Returns None for non-Mongo backends
    (the check doesn't apply).
    """
    from regstack.backends.base import BackendKind

    backend = build_backend(config)
    try:
        if backend.kind is not BackendKind.MONGO:
            return None
        from regstack.backends.mongo import MongoBackend

        assert isinstance(backend, MongoBackend)
        info = await backend.database.command("buildInfo")
        version = str(info.get("version", ""))
        if not version:
            return CheckResult.warned(
                "mongo server", "buildInfo returned no version; verify against CVE-2025-14847"
            )
        ok, detail = _assess_mongo_server_version(version)
        if ok:
            return CheckResult.passed("mongo server", detail)
        return CheckResult.warned("mongo server", detail)
    except Exception as exc:
        return CheckResult.warned("mongo server", f"version check failed: {exc}")
    finally:
        await backend.aclose()


def _check_email_factory(config: RegStackConfig) -> CheckResult:
    try:
        service = build_email_service(config.email)
    except Exception as exc:
        return CheckResult(
            "email backend", False, f"backend {config.email.backend!r} failed to instantiate: {exc}"
        )
    return CheckResult("email backend", True, f"{config.email.backend} → {type(service).__name__}")


def _check_dns(config: RegStackConfig) -> list[CheckResult]:
    sender = config.email.from_address
    try:
        domain = sender.split("@", 1)[1]
    except IndexError:
        return [CheckResult("dns sender", False, f"invalid sender: {sender!r}")]
    out: list[CheckResult] = []
    out.append(_dig(domain, "MX", "dns mx"))
    out.append(_dig(domain, "TXT", "dns spf", needle="v=spf1"))
    out.append(_dig(f"_dmarc.{domain}", "TXT", "dns dmarc", needle="v=DMARC1"))
    return out


def _dig(name: str, rtype: str, label: str, *, needle: str | None = None) -> CheckResult:
    try:
        answers = dns.resolver.resolve(name, rtype, lifetime=5.0)
    except dns.resolver.NXDOMAIN:
        return CheckResult(label, False, f"{name} → NXDOMAIN")
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.DNSException) as exc:
        return CheckResult(label, False, f"{name} → {exc}")
    if needle is not None:
        joined = "\n".join(str(rdata) for rdata in answers)
        if needle not in joined:
            return CheckResult(label, False, f"no {needle!r} record on {name}")
    return CheckResult(label, True, f"{name} ok ({len(answers)} record(s))")


async def _send_test_email(config: RegStackConfig, to: str) -> CheckResult:
    from regstack.email.base import EmailMessage

    try:
        service = build_email_service(config.email)
        await service.send(
            EmailMessage(
                to=to,
                subject=f"[{config.app_name}] regstack doctor probe",
                html="<p>regstack doctor probe — if you can read this, your email backend works.</p>",
                text="regstack doctor probe — if you can read this, your email backend works.",
                from_address=config.email.from_address,
                from_name=config.email.from_name or config.app_name,
            )
        )
        return CheckResult(
            "email send", True, f"probe delivered to {to} via {config.email.backend}"
        )
    except Exception as exc:
        return CheckResult("email send", False, f"send failed: {exc}")

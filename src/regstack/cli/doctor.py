from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import click
import dns.resolver

from regstack.backends.factory import build_backend, detect_backend_kind
from regstack.cli._runtime import load_runtime_config
from regstack.email.factory import build_email_service


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


@click.command(
    name="doctor",
    help="Read-only validation of the loaded regstack configuration.",
)
@click.option(
    "--config",
    "toml_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to regstack.toml (default: search cwd / $REGSTACK_CONFIG).",
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
def doctor(toml_path: Path | None, check_dns: bool, test_recipient: str | None) -> None:
    results = asyncio.run(
        _run(toml_path=toml_path, check_dns=check_dns, test_recipient=test_recipient)
    )
    failed = sum(1 for r in results if not r.ok)
    for r in results:
        symbol = click.style("✔", fg="green") if r.ok else click.style("✘", fg="red")
        click.echo(f"{symbol} {r.name}: {r.detail}")
    if failed:
        click.echo(click.style(f"\n{failed} check(s) failed.", fg="red"), err=True)
    sys.exit(failed)


async def _run(
    *, toml_path: Path | None, check_dns: bool, test_recipient: str | None
) -> list[CheckResult]:
    out: list[CheckResult] = []

    config = load_runtime_config(toml_path)

    secret_value = config.jwt_secret.get_secret_value()
    if not secret_value:
        out.append(CheckResult("jwt secret", False, "missing — run `regstack init`"))
    elif len(secret_value) < 32:
        out.append(
            CheckResult("jwt secret", False, f"too short ({len(secret_value)} chars; need ≥32)")
        )
    else:
        out.append(CheckResult("jwt secret", True, f"present ({len(secret_value)} chars)"))

    out.append(await _check_backend(config))
    out.append(await _check_schema(config))
    out.append(_check_email_factory(config))

    if check_dns:
        out.extend(_check_dns(config))

    if test_recipient:
        out.append(await _send_test_email(config, test_recipient))

    return out


async def _check_backend(config) -> CheckResult:
    kind = detect_backend_kind(config.database_url.get_secret_value())
    backend = build_backend(config)
    try:
        await backend.ping()
        return CheckResult("backend", True, f"{kind} reachable")
    except Exception as exc:
        return CheckResult("backend", False, f"{kind} unreachable: {exc}")
    finally:
        await backend.aclose()


async def _check_schema(config) -> CheckResult:
    """Confirm the schema/indexes are installed.

    For Mongo we look for the canonical email_unique + jti_unique indexes;
    for SQL backends we just attempt a roundtrip query against the users
    table (Alembic migrations are idempotent, so absence is detected by
    a missing-table error from the driver).
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
        # SQL backends: attempt a count query — fails with a missing-table error if not installed.
        await backend.users.count()
        return CheckResult("schema", True, "users table present")
    except Exception as exc:
        return CheckResult("schema", False, f"check failed: {exc}")
    finally:
        await backend.aclose()


def _check_email_factory(config) -> CheckResult:
    try:
        service = build_email_service(config.email)
    except Exception as exc:
        return CheckResult(
            "email backend", False, f"backend {config.email.backend!r} failed to instantiate: {exc}"
        )
    return CheckResult("email backend", True, f"{config.email.backend} → {type(service).__name__}")


def _check_dns(config) -> list[CheckResult]:
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


async def _send_test_email(config, to: str) -> CheckResult:
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
                from_name=config.email.from_name,
            )
        )
        return CheckResult(
            "email send", True, f"probe delivered to {to} via {config.email.backend}"
        )
    except Exception as exc:
        return CheckResult("email send", False, f"send failed: {exc}")

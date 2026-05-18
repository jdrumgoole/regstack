"""Thin async wrapper around aioboto3 for the SES wizard's AWS calls.

Split into its own module so the routes layer can patch the public
functions in tests without touching ``aioboto3.Session`` directly.

Three calls live here:

- :func:`probe_credentials` — STS ``GetCallerIdentity``. Cheapest
  way to confirm the chosen credential source actually authenticates;
  doesn't require any SES permission.
- :func:`probe_sender_identity` — SES ``GetIdentityVerificationAttributes``
  on both the full email and the domain. Reports per-identity status.
- :func:`probe_sandbox_state` — SES ``GetAccount`` (preferred) with a
  fallback to ``GetSendQuota`` heuristic. Tolerant of ``AccessDenied``:
  returns "unknown" rather than raising so a least-privilege IAM
  policy doesn't block the wizard.
- :func:`send_test_email` — SES ``SendEmail`` for the live probe at
  step 6.

All four return small typed result dataclasses the routes layer
shovels into the SPA response payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

try:
    import aioboto3  # type: ignore  # aioboto3 lacks py.typed; CI lints without --extra ses
except ImportError as _exc:  # pragma: no cover — extras gate
    aioboto3 = None
    _IMPORT_ERROR: ImportError | None = _exc
else:
    _IMPORT_ERROR = None


CredentialSource = Literal["profile", "explicit", "chain"]
VerificationStatus = Literal["verified", "pending", "failed", "unknown"]


@dataclass(slots=True, frozen=True)
class CredentialProbe:
    ok: bool
    account_id: str | None = None
    arn: str | None = None
    error: str | None = None


@dataclass(slots=True, frozen=True)
class IdentityProbe:
    address_status: VerificationStatus
    domain_status: VerificationStatus
    error: str | None = None


@dataclass(slots=True, frozen=True)
class SandboxProbe:
    in_sandbox: bool
    detection: Literal["api", "quota_heuristic", "unknown"]
    error: str | None = None


@dataclass(slots=True, frozen=True)
class TestSendProbe:
    ok: bool
    message_id: str | None = None
    error: str | None = None


def aws_available() -> bool:
    """Whether the ``ses`` extra (aioboto3) is importable.

    Used by the routes layer to short-circuit AWS-touching steps
    with a clear error when the extra is missing — though the lazy
    click group should catch this at invocation time, the test suite
    exercises the routes directly without going through click.
    """
    return aioboto3 is not None


def _session(
    *,
    source: CredentialSource,
    profile: str | None,
    access_key_id: str | None,
    secret_access_key: str | None,
) -> Any:
    if aioboto3 is None:  # pragma: no cover — extras gate
        raise RuntimeError(
            "aioboto3 not installed — install the 'ses' extra: `pip install 'regstack[ses]'`."
        )
    if source == "profile":
        return aioboto3.Session(profile_name=profile)
    if source == "explicit":
        return aioboto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )
    return aioboto3.Session()


async def probe_credentials(
    *,
    region: str,
    source: CredentialSource,
    profile: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> CredentialProbe:
    """Resolve credentials by calling STS ``GetCallerIdentity``.

    STS is global; the region argument is passed only so a misconfigured
    profile points at the same region the rest of the wizard uses.
    """
    try:
        session = _session(
            source=source,
            profile=profile,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
        async with session.client("sts", region_name=region) as sts:
            identity = await sts.get_caller_identity()
    except Exception as exc:
        return CredentialProbe(ok=False, error=_describe(exc))
    return CredentialProbe(
        ok=True,
        account_id=str(identity.get("Account") or ""),
        arn=str(identity.get("Arn") or ""),
    )


async def probe_sender_identity(
    *,
    region: str,
    from_address: str,
    source: CredentialSource,
    profile: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> IdentityProbe:
    """Check SES verification status for the sender's address and domain.

    SES accepts either an email-address-level verification (verify
    a specific ``noreply@example.com``) or a domain-level verification
    (verify ``example.com`` and any address under it works). We report
    both so the SPA can render the right next-step link.
    """
    if "@" not in from_address:
        return IdentityProbe(
            address_status="unknown",
            domain_status="unknown",
            error="from_address missing '@'",
        )
    domain = from_address.split("@", 1)[1].lower()
    try:
        session = _session(
            source=source,
            profile=profile,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
        async with session.client("ses", region_name=region) as ses:
            result = await ses.get_identity_verification_attributes(
                Identities=[from_address, domain],
            )
    except Exception as exc:
        return IdentityProbe(
            address_status="unknown",
            domain_status="unknown",
            error=_describe(exc),
        )
    attrs = result.get("VerificationAttributes") or {}
    return IdentityProbe(
        address_status=_status(attrs.get(from_address, {}).get("VerificationStatus")),
        domain_status=_status(attrs.get(domain, {}).get("VerificationStatus")),
    )


async def probe_sandbox_state(
    *,
    region: str,
    source: CredentialSource,
    profile: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> SandboxProbe:
    """Detect whether the account is in the SES sandbox.

    Preferred path: ``ses.get_account()['ProductionAccessEnabled']``
    (added to the SES API in 2021). Fallback: the historic quota
    heuristic (sandbox accounts have a 200-message-per-day cap that
    typically isn't increased without a graduation request).

    Tolerant of ``AccessDenied`` on both — some operator IAM
    policies block ``ses:GetAccount`` even when they allow
    ``ses:SendEmail``. In that case we return ``in_sandbox=False`` +
    ``detection="unknown"`` and the routes layer surfaces a non-
    blocking advisory.
    """
    try:
        session = _session(
            source=source,
            profile=profile,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
        async with session.client("ses", region_name=region) as ses:
            try:
                account = await ses.get_account()
                # `ProductionAccessEnabled` is the canonical key. When
                # absent (older SDK / control-plane version) we drop
                # to the quota heuristic.
                if "ProductionAccessEnabled" in account:
                    return SandboxProbe(
                        in_sandbox=not bool(account["ProductionAccessEnabled"]),
                        detection="api",
                    )
            except Exception:
                pass

            quota = await ses.get_send_quota()
            # Sandbox heuristic: 200 messages/day + 1 message/sec is
            # the default sandbox cap. Anything strictly above is a
            # graduated account.
            max_24h = float(quota.get("Max24HourSend") or 0)
            max_rate = float(quota.get("MaxSendRate") or 0)
            in_sandbox = max_24h <= 200.0 and max_rate <= 1.0
            return SandboxProbe(in_sandbox=in_sandbox, detection="quota_heuristic")
    except Exception as exc:
        return SandboxProbe(in_sandbox=False, detection="unknown", error=_describe(exc))


async def send_test_email(
    *,
    region: str,
    from_address: str,
    to_address: str,
    source: CredentialSource,
    profile: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
) -> TestSendProbe:
    subject = "regstack SES wizard — test send"
    body = (
        "This is a test message from the `regstack ses setup` wizard.\n"
        "If you can read it, your SES configuration is working.\n"
    )
    try:
        session = _session(
            source=source,
            profile=profile,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
        async with session.client("ses", region_name=region) as ses:
            response = await ses.send_email(
                Source=from_address,
                Destination={"ToAddresses": [to_address]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                },
            )
    except Exception as exc:
        return TestSendProbe(ok=False, error=_describe(exc))
    return TestSendProbe(ok=True, message_id=str(response.get("MessageId") or ""))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status(raw: Any) -> VerificationStatus:
    """Map SES's ``VerificationStatus`` strings to our enum.

    SES returns ``"Success"`` for verified, ``"Pending"`` for
    waiting-on-DNS, ``"Failed"`` for the DNS-record-missing branch,
    and omits the key entirely for an unknown identity.
    """
    if raw == "Success":
        return "verified"
    if raw == "Pending":
        return "pending"
    if raw == "Failed":
        return "failed"
    return "unknown"


def _describe(exc: BaseException) -> str:
    """Short human-readable error string for the SPA.

    Doesn't try to be exhaustive — the operator who can read AWS
    error codes also knows what they mean. We strip the boto3-specific
    prefix and cap length so a single long traceback line doesn't
    blow up the SPA layout.
    """
    text = f"{type(exc).__name__}: {exc}"
    # Cap at 400 chars; SES error bodies are usually shorter.
    return text[:400]


__all__ = [
    "CredentialProbe",
    "IdentityProbe",
    "SandboxProbe",
    "TestSendProbe",
    "aws_available",
    "probe_credentials",
    "probe_sandbox_state",
    "probe_sender_identity",
    "send_test_email",
]

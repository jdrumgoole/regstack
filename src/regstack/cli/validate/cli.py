"""``regstack validate`` — end-to-end probe of a deployed installation.

The Click command's docstring is the operator runbook; see
``regstack validate --help`` to read it.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import click

from regstack.cli.validate.http import HttpProbe
from regstack.cli.validate.logtail import LogTailer, parse_log_source
from regstack.cli.validate.phases import (
    account as account_phase,
)
from regstack.cli.validate.phases import (
    cleanup as cleanup_phase,
)
from regstack.cli.validate.phases import (
    core_auth as core_auth_phase,
)
from regstack.cli.validate.phases import (
    feature_discover as feature_phase,
)
from regstack.cli.validate.phases import (
    oauth as oauth_phase,
)
from regstack.cli.validate.phases import (
    password_reset as reset_phase,
)
from regstack.cli.validate.phases import (
    reachability as reachability_phase,
)
from regstack.cli.validate.phases import (
    sms_mfa as sms_phase,
)
from regstack.cli.validate.report import failures, render_human, render_json
from regstack.cli.validate.runner import (
    RunnerContext,
    ValidationRunner,
    make_probe_identity,
)

if TYPE_CHECKING:
    pass


_HELP = """\
End-to-end probe of a deployed regstack installation.

Registers a throwaway user against the live JSON API, walks through every
auth flow (verify, login, reset, change-email, OAuth start, SMS 2FA if
enabled), then deletes the user. Exit code = number of failed checks.

\b
─── Preparation (must be done BEFORE running this command) ────────────────

\b
1. Configure the deployment's email backend to `console`.
   In regstack.toml on the target host:
       [email]
       backend = "console"
   Real SMTP/SES backends are rejected because the validator cannot
   read the token out of an email it never sees. Switch to console for
   the duration of the probe; revert afterwards.

\b
2. Set `email.log_bodies = true` in regstack.toml.
   The console backend logs To/From/Subject at INFO unconditionally,
   but the message BODY (which carries the verification / reset /
   change-email URL) is at DEBUG by default. The `log_bodies` flag
   promotes the body to INFO so the validator can scrape one-time
   tokens via a normal log stream. The verify phase fails with a
   hint pointing here if it isn't set.

\b
3. If you intend to probe SMS 2FA, set `sms.backend = "null"`.
   The bundled `null` backend logs the SMS body at INFO so the
   validator can scrape the 6-digit code. `sns` and `twilio` backends
   will not work. Pass --phone <E.164 number> on the command line;
   the number is only ever logged, never dialled.

\b
4. Make the deployment's stdout readable from where you run validate.
   Pick ONE and pass it via --log-source:

\b
     --log-source file:/var/log/regstack.log
         Tail a local file. systemd:
         `StandardOutput=append:/var/log/regstack.log`. Docker:
         `docker run ... &> /var/log/regstack.log`.

\b
     --log-source ssh:user@host:/var/log/regstack.log
         Tail a file on a remote host over SSH. Uses key-based auth
         only (BatchMode=yes). Verify `ssh user@host tail -F /path`
         works interactively first.

\b
     --log-source docker:<container-name>
         Run `docker logs -f --since 1s <container>` on the local
         docker socket. Requires docker access for the running user.

\b
     --log-source cmd:'journalctl -fu regstack.service'
         Escape hatch. Any command that streams the deployment's
         stdout to its own stdout.

\b
5. (Optional) Ensure outbound connectivity from `validate` to the API.
   The validator is an HTTP client. If --url is behind a VPN, bastion,
   or mTLS, set those up before running.

\b
─── What gets created and destroyed on the deployment ─────────────────────

\b
- One user `validate-<uuid>@<probe-email-domain>` is registered,
  verified, has its profile patched, password reset, email changed,
  password changed, and (if --phone given) phone-2FA enrolled then
  disabled.
- At the end the user is removed via DELETE /account. The bearer
  token is also blacklisted. The probe never touches any other user.
- If the run aborts mid-flow, the validator still tries DELETE
  /account in a finally block. If even that fails, the probe email
  is printed so you can remove it manually.
- `--no-cleanup` skips the delete (debugging only).

\b
─── Example ────────────────────────────────────────────────────────────────

\b
   regstack validate \\
       --url https://staging.example.com/api/auth \\
       --log-source ssh:deploy@staging.example.com:/var/log/regstack.log \\
       --phone +15551234567

\b
─── Phases ────────────────────────────────────────────────────────────────

\b
   reachability → feature-discovery →
   register → verify → login → /me → logout + blacklist → re-login →
   PATCH /me → change-password → change-email → password reset →
   OAuth start (if enabled) → SMS 2FA (if enabled + --phone given) →
   cleanup
"""


@click.command(name="validate", help=_HELP)
@click.option(
    "--url",
    required=True,
    help="Base URL where the regstack JSON router is mounted "
    "(e.g. https://host.example.com/api/auth).",
)
@click.option(
    "--log-source",
    "log_source_spec",
    default=None,
    help="Where to tail the deployment's stdout. file:/PATH, "
    "ssh:user@host:/PATH, docker:CONTAINER, or cmd:'<shell>'. "
    "Required unless every token-bearing phase is --skip'd.",
)
@click.option(
    "--phone",
    "phone_number",
    default=None,
    help="E.164 phone number for the SMS 2FA probe. Without this the "
    "SMS phase is skipped even if the deployment has it mounted.",
)
@click.option(
    "--probe-email-domain",
    default="regstack-probe.example",
    show_default=True,
    help="Domain for the throwaway user's email address.",
)
@click.option(
    "--password",
    default=None,
    help="Override the probe user's initial password (default: random).",
)
@click.option(
    "--skip",
    "skip_phases",
    default="",
    help="Comma-separated phases to skip: oauth,sms,reset,account.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON report.")
@click.option(
    "--no-cleanup",
    is_flag=True,
    help="Leave the probe user behind (debugging only).",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Log every HTTP request and tailed log line.",
)
@click.option(
    "--insecure",
    is_flag=True,
    help="Skip TLS verification (self-signed staging certs).",
)
@click.option(
    "--timeout",
    type=float,
    default=10.0,
    show_default=True,
    help="Per-request HTTP timeout in seconds.",
)
def validate(
    url: str,
    log_source_spec: str | None,
    phone_number: str | None,
    probe_email_domain: str,
    password: str | None,
    skip_phases: str,
    as_json: bool,
    no_cleanup: bool,
    verbose: bool,
    insecure: bool,
    timeout: float,
) -> None:
    skipped = {s.strip() for s in skip_phases.split(",") if s.strip()}
    # Note: --log-source is *not* hard-required at the CLI level. Phases
    # that need a token (verify, password reset, change-email, SMS 2FA)
    # report their own failed CheckResult if the tailer is None, so the
    # operator still gets a structured run with clear "no --log-source"
    # failures for each affected phase. Hard-requiring at CLI level
    # would short-circuit the feature-discovery output, which is useful
    # diagnostic info in its own right.

    exit_code = asyncio.run(
        _run(
            url=url,
            log_source_spec=log_source_spec,
            phone_number=phone_number,
            probe_email_domain=probe_email_domain,
            password=password,
            skipped=skipped,
            as_json=as_json,
            no_cleanup=no_cleanup,
            verbose=verbose,
            insecure=insecure,
            timeout=timeout,
        )
    )
    sys.exit(exit_code)


async def _run(
    *,
    url: str,
    log_source_spec: str | None,
    phone_number: str | None,
    probe_email_domain: str,
    password: str | None,
    skipped: set[str],
    as_json: bool,
    no_cleanup: bool,
    verbose: bool,
    insecure: bool,
    timeout: float,
) -> int:
    tailer: LogTailer | None = None
    if log_source_spec is not None:
        try:
            spec = parse_log_source(log_source_spec)
        except ValueError as exc:
            click.echo(f"error: {exc}", err=True)
            return 2
        tailer = LogTailer(spec, verbose=verbose)
        await tailer.start()

    identity = make_probe_identity(email_domain=probe_email_domain, password=password)
    http = HttpProbe(url, timeout=timeout, verbose=verbose, verify=not insecure)
    ctx = RunnerContext(
        http=http,
        tailer=tailer,
        identity=identity,
        skipped=skipped,
        phone_number=phone_number,
        no_cleanup=no_cleanup,
    )

    phases = [
        ("reachability", reachability_phase.run),
        ("features", feature_phase.run),
        ("register", core_auth_phase.run),
        ("account", account_phase.run),
        ("password-reset", reset_phase.run),
        ("oauth", oauth_phase.run),
        ("sms-2fa", sms_phase.run),
    ]
    runner = ValidationRunner(ctx, phases, cleanup_phase=cleanup_phase.run)

    try:
        results = await runner.run()
    finally:
        if tailer is not None:
            await tailer.close()
        await http.close()

    if as_json:
        click.echo(render_json(results))
    else:
        click.echo(render_human(results))

    fail_count = failures(results)
    if fail_count and not as_json:
        click.echo(click.style(f"\n{fail_count} check(s) failed.", fg="red"), err=True)
    if no_cleanup:
        click.echo(
            click.style(
                f"\nleft probe user {identity.email} on deployment (--no-cleanup).",
                fg="yellow",
            ),
            err=True,
        )
    return fail_count

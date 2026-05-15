"""Regexes for scraping one-time tokens out of console-backend log lines.

The console email backend logs the rendered text body of every email
(when ``email.log_bodies = true`` in regstack.toml — see the validate
runbook). Each body contains exactly one URL with a ``?token=...``
parameter, which is the one-time proof for that flow.

The ``null`` SMS backend logs the SMS body unconditionally; bodies use
fixed phrases (``sign-in code: NNNNNN``, ``verification code: NNNNNN``)
so the codes can be pulled out without matching the whole template.

The patterns are deliberately permissive on the URL prefix — hosts mount
the verification page at whatever path they like — and strict on the
query-string shape so a stray ``token`` query param somewhere else in a
log line can't be mistaken for the real one.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_TOKEN_URL = r"https?://[^\s>'\"]+?\?token=[A-Za-z0-9._\-+/=%]+"

VERIFICATION_URL_RE = re.compile(rf"(?P<url>{_TOKEN_URL})(?=\b|$)")
"""Matches the verification URL in console-email body output. The
``url`` named group is the full clickable link."""

# Same shape; the path tells the flows apart (``/verify`` vs
# ``/reset-password`` vs ``/confirm-email-change``), so a single
# tightly-bounded URL regex is enough — `LogTailer.expect_url` just
# greps for whichever substring the caller passes.

LOGIN_MFA_CODE_RE = re.compile(r"sign-in code:\s*(?P<code>\d{4,10})")
"""Matches the 6-digit code in the bundled ``sms_login_mfa.txt``
template. Range is 4-10 to absorb hosts that lower
``sms_code_length``."""

PHONE_SETUP_CODE_RE = re.compile(r"verification code:\s*(?P<code>\d{4,10})")
"""Matches the 6-digit code in the bundled ``sms_phone_setup.txt``
template."""

CONSOLE_BODY_MARKER_RE = re.compile(r"\[regstack/console-email\] text body:")
"""Marker emitted by :class:`~regstack.email.console.ConsoleEmailService`
right before the rendered text body. The log-tail handshake phase
uses this to confirm bodies are reaching the tail stream at all (i.e.
that ``email.log_bodies = true``)."""


def extract_token_from_url(url: str) -> str | None:
    """Return the ``token`` query parameter, or ``None`` if absent."""
    qs = parse_qs(urlparse(url).query)
    values = qs.get("token")
    if not values:
        return None
    return values[0]

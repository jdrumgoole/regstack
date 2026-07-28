from __future__ import annotations

from urllib.parse import quote

REDACTED = "[REDACTED]"
"""Placeholder substituted for a single-use token in a loggable URL."""


def redact_token(url: str, token: str) -> str:
    """Return ``url`` with every occurrence of ``token`` replaced.

    Verification, password-reset and email-change links carry a live
    single-use credential. Which *part* of the URL holds it depends on
    the host's ``*_url_template`` — a query parameter by default, but a
    path segment or fragment when the host uses hash routing — so the
    redaction matches the token string itself rather than a query key.

    Args:
        url: The composed link, as emailed to the user.
        token: The raw token embedded in ``url``.

    Returns:
        The URL with each occurrence of the token (raw and
        percent-encoded) replaced by :data:`REDACTED`. Returns ``url``
        unchanged when ``token`` is empty.
    """
    if not token:
        return url
    safe = url.replace(token, REDACTED)
    encoded = quote(token, safe="")
    if encoded != token:
        safe = safe.replace(encoded, REDACTED)
    return safe

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from regstack.models.user import BaseUser


def require_password_set(user: BaseUser) -> None:
    """Reject password-confirmation flows for OAuth-only users.

    Endpoints that re-prompt for the current password (change-password,
    change-email, delete-account, phone setup/disable) need *some*
    password on file to compare against. OAuth-only users have
    ``hashed_password = None`` — direct them to forgot-password, which
    doubles as a "set initial password" path. Callers should follow this
    with ``assert user.hashed_password is not None`` to narrow the type.
    """
    if user.hashed_password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No password set on this account. "
                "Use forgot-password to set one before changing account details."
            ),
        )

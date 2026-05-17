from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, IndexModel

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

    from regstack.backends.mongo.client import MongoDoc
    from regstack.config.schema import RegStackConfig

log = logging.getLogger(__name__)


async def install_indexes(db: AsyncDatabase[MongoDoc], config: RegStackConfig) -> None:
    """Create the indexes regstack relies on. Safe to call repeatedly."""
    users = db[config.user_collection]
    await _drop_conflicting_email_index(users)
    await users.create_indexes(
        [IndexModel([("email", ASCENDING)], unique=True, name="email_unique")]
    )

    blacklist = db[config.blacklist_collection]
    # TTL on `exp` lets MongoDB reap revoked tokens when they would have
    # expired anyway. expireAfterSeconds=0 means "delete when the date is
    # in the past" — the value at `exp` is the deletion deadline.
    await blacklist.create_indexes(
        [
            IndexModel([("jti", ASCENDING)], unique=True, name="jti_unique"),
            IndexModel([("exp", ASCENDING)], expireAfterSeconds=0, name="exp_ttl"),
        ]
    )

    pending = db[config.pending_collection]
    await pending.create_indexes(
        [
            IndexModel([("email", ASCENDING)], unique=True, name="pending_email_unique"),
            IndexModel([("token_hash", ASCENDING)], unique=True, name="pending_token_unique"),
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="pending_ttl"),
        ]
    )

    attempts = db[config.login_attempt_collection]
    # Sparse-ish TTL — rows survive `login_lockout_window_seconds` after
    # `when`. The TTL value comes from config so tightening the lockout
    # window also tightens cleanup.
    await attempts.create_indexes(
        [
            IndexModel([("email", ASCENDING), ("when", ASCENDING)], name="email_when"),
            IndexModel(
                [("when", ASCENDING)],
                expireAfterSeconds=config.login_lockout_window_seconds,
                name="when_ttl",
            ),
        ]
    )

    mfa = db[config.mfa_code_collection]
    await mfa.create_indexes(
        [
            IndexModel(
                [("user_id", ASCENDING), ("kind", ASCENDING)],
                unique=True,
                name="user_kind_unique",
            ),
            IndexModel([("expires_at", ASCENDING)], expireAfterSeconds=0, name="mfa_ttl"),
        ]
    )

    oauth_identities = db[config.oauth_identity_collection]
    await oauth_identities.create_indexes(
        [
            IndexModel(
                [("provider", ASCENDING), ("subject_id", ASCENDING)],
                unique=True,
                name="provider_subject_unique",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("provider", ASCENDING)],
                unique=True,
                name="user_provider_unique",
            ),
        ]
    )

    oauth_states = db[config.oauth_state_collection]
    await oauth_states.create_indexes(
        [
            IndexModel(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                name="oauth_state_ttl",
            ),
        ]
    )
    await _ensure_oauth_states_validator(db, config.oauth_state_collection)

    log.info("regstack indexes installed on database %s", db.name)


async def _drop_conflicting_email_index(users: Any) -> None:
    """Drop an unnamed unique index on ``email`` left over from a host's
    pre-regstack auth code.

    Mongo cannot rename an index in place. Hosts that previously ran
    ``db.users.create_index([("email", ASCENDING)], unique=True)`` from
    their own code end up with the auto-generated name ``email_1`` on
    the same key + uniqueness regstack wants under ``email_unique``.
    A fresh ``install_indexes`` then raises ``IndexOptionsConflict``
    on its first boot under regstack.

    This helper detects ANY index over exactly ``{"email": 1}`` with
    ``unique=True`` that is not already named ``email_unique``, and
    drops it so the canonical-name create on the next line succeeds.
    We deliberately do not require the legacy name to be exactly
    ``email_1`` — any other rename (e.g. a host that named theirs
    ``users_email_uq``) would hit the same conflict.

    Idempotent — re-running on a healthy database is a no-op because
    the loop only matches indexes that aren't already the canonical
    one. Safe even if the collection doesn't exist yet (the
    ``index_information`` call returns an empty dict).
    """
    try:
        existing = await users.index_information()
    except Exception:  # pragma: no cover — defensive; missing namespace
        return

    for name, info in existing.items():
        if name in ("_id_", "email_unique"):
            continue
        key = info.get("key")
        if key != [("email", 1)]:
            continue
        if not info.get("unique"):
            continue
        log.warning(
            "Dropping legacy unique-on-email index %r on %s.users to "
            "make room for email_unique (regstack canonical name).",
            name, users.database.name,
        )
        await users.drop_index(name)


async def _ensure_oauth_states_validator(db: AsyncDatabase[MongoDoc], collection_name: str) -> None:
    """Pin ``oauth_states.mode`` to ``signin`` / ``link`` at the DB level.

    Mirrors the SQL backend's ``CheckConstraint("mode IN ('signin', 'link')")``.
    ``OAuthState.model_validate()`` already enforces this at the application
    layer on every read, so this is defence-in-depth rather than a closed
    exploit path — flagged as I-5 in the 2026-05-15 / 2026-05-16 security
    reviews.

    Uses ``validationLevel="moderate"`` so re-running ``install_indexes``
    on a populated collection does not retroactively reject pre-existing
    rows; the constraint applies to inserts and updates that touch
    ``mode``.
    """
    from pymongo.errors import OperationFailure

    validator = {
        "$jsonSchema": {
            "bsonType": "object",
            "properties": {
                "mode": {"enum": ["signin", "link"]},
            },
        }
    }
    try:
        await db.command(
            {
                "collMod": collection_name,
                "validator": validator,
                "validationLevel": "moderate",
                "validationAction": "error",
            }
        )
    except OperationFailure as exc:
        # collMod fails on a non-existent collection. Create it with the
        # validator attached instead; either path leaves the collection
        # with the schema in place.
        if "NamespaceNotFound" not in str(exc) and exc.code != 26:  # 26 = NamespaceNotFound
            raise
        await db.create_collection(
            collection_name,
            validator=validator,
            validationLevel="moderate",
            validationAction="error",
        )

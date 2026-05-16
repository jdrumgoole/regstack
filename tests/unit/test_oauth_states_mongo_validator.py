"""Pin the $jsonSchema validator on oauth_states.mode.

After install_indexes, the Mongo collection should reject inserts with
an invalid ``mode``. Mirrors the SQL backend's CheckConstraint.

Flagged as I-5 in the 2026-05-15 / 2026-05-16 security reviews.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from pymongo.errors import WriteError

from regstack.backends.mongo.indexes import install_indexes
from regstack.config.schema import RegStackConfig


@pytest.fixture
def backend_kind() -> str:
    """Mongo-only: this is a $jsonSchema-validator test."""
    return "mongo"


@pytest_asyncio.fixture
async def mongo_db(mongo_client):
    """Fresh DB with the regstack schema installed for this test."""
    db = mongo_client.get_default_database()
    cfg = RegStackConfig(
        jwt_secret=secrets.token_urlsafe(32),
        oauth_state_collection=f"oauth_states_{secrets.token_hex(4)}",
    )
    await install_indexes(db, cfg)
    yield db, cfg
    await db[cfg.oauth_state_collection].drop()


def _state_doc(mode: str) -> dict:
    now = datetime.now(tz=UTC)
    return {
        "_id": secrets.token_urlsafe(16),
        "provider": "google",
        "code_verifier": "x" * 64,
        "nonce": secrets.token_urlsafe(16),
        "redirect_to": "/account/me",
        "mode": mode,
        "linking_user_id": None,
        "created_at": now,
        "expires_at": now + timedelta(minutes=5),
        "result_token": None,
    }


async def test_signin_mode_accepted(mongo_db) -> None:
    db, cfg = mongo_db
    await db[cfg.oauth_state_collection].insert_one(_state_doc("signin"))


async def test_link_mode_accepted(mongo_db) -> None:
    db, cfg = mongo_db
    await db[cfg.oauth_state_collection].insert_one(_state_doc("link"))


async def test_invalid_mode_rejected(mongo_db) -> None:
    db, cfg = mongo_db
    with pytest.raises(WriteError):
        await db[cfg.oauth_state_collection].insert_one(_state_doc("admin"))


async def test_invalid_mode_empty_rejected(mongo_db) -> None:
    db, cfg = mongo_db
    with pytest.raises(WriteError):
        await db[cfg.oauth_state_collection].insert_one(_state_doc(""))


async def test_install_indexes_idempotent(mongo_db) -> None:
    """Running install_indexes a second time on a populated collection
    must NOT raise — moderate validation level should leave existing
    docs alone."""
    db, cfg = mongo_db
    # Insert one valid doc.
    await db[cfg.oauth_state_collection].insert_one(_state_doc("signin"))
    # Re-run install_indexes; should be a no-op for already-valid data.
    await install_indexes(db, cfg)

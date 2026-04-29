"""SQLAlchemy 2.x schema for the SQL backends.

We use Core (not the ORM) — repos hand-craft small, async-friendly
queries. Tables are kept on a single MetaData so Alembic autogenerate can
diff against the live DB.

Type choices:

- IDs are UUID4 strings (stored as 36-char ``CHAR``). SQLite has no
  native UUID type, and stringly-typed UUIDs work uniformly across
  Postgres and SQLite without driver-specific casting.
- ``UtcDateTime()`` everywhere, so reads come back tz-aware
  on Postgres. SQLite ignores the timezone but the SQLAlchemy adapter
  preserves it through encode/decode.
- ``Boolean`` rather than INT(0|1) so SQLite stores 0/1 and Postgres
  stores native booleans without casting.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from regstack.backends.sql.types import UtcDateTime

# Single MetaData so Alembic autogenerate sees every table.
metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_label)s",
        "uq": "uq_%(table_name)s_%(column_0_N_label)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def _users(table_name: str) -> Table:
    return Table(
        table_name,
        metadata,
        Column("id", String(36), primary_key=True),
        Column("email", String(320), nullable=False),
        # Nullable since 0.3.0 — OAuth-only users never set a password.
        # Existing rows always have a value; only new OAuth signups
        # land with NULL.
        Column("hashed_password", Text, nullable=True),
        Column("is_active", Boolean, nullable=False, default=True),
        Column("is_verified", Boolean, nullable=False, default=False),
        Column("is_superuser", Boolean, nullable=False, default=False),
        Column("full_name", String(200), nullable=True),
        Column("phone_number", String(20), nullable=True),
        Column("is_mfa_enabled", Boolean, nullable=False, default=False),
        Column("created_at", UtcDateTime(), nullable=False),
        Column("updated_at", UtcDateTime(), nullable=False),
        Column("last_login", UtcDateTime(), nullable=True),
        Column("tokens_invalidated_after", UtcDateTime(), nullable=True),
        UniqueConstraint("email", name="email_unique"),
    )


def _pending(table_name: str) -> Table:
    return Table(
        table_name,
        metadata,
        Column("id", String(36), primary_key=True),
        Column("email", String(320), nullable=False),
        Column("hashed_password", Text, nullable=False),
        Column("full_name", String(200), nullable=True),
        Column("token_hash", String(64), nullable=False),
        Column("created_at", UtcDateTime(), nullable=False),
        Column("expires_at", UtcDateTime(), nullable=False),
        UniqueConstraint("email", name="pending_email_unique"),
        UniqueConstraint("token_hash", name="pending_token_unique"),
        Index("ix_pending_expires_at", "expires_at"),
    )


def _blacklist(table_name: str) -> Table:
    return Table(
        table_name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("jti", String(64), nullable=False),
        Column("exp", UtcDateTime(), nullable=False),
        UniqueConstraint("jti", name="jti_unique"),
        Index("ix_blacklist_exp", "exp"),
    )


def _login_attempts(table_name: str) -> Table:
    return Table(
        table_name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("email", String(320), nullable=False),
        Column("when", UtcDateTime(), nullable=False),
        Column("ip", String(64), nullable=True),
        Index("ix_login_attempts_email_when", "email", "when"),
    )


def _mfa_codes(table_name: str) -> Table:
    return Table(
        table_name,
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", String(36), nullable=False),
        Column("kind", String(32), nullable=False),
        Column("code_hash", String(64), nullable=False),
        Column("expires_at", UtcDateTime(), nullable=False),
        Column("attempts", Integer, nullable=False, default=0),
        Column("max_attempts", Integer, nullable=False, default=5),
        Column("created_at", UtcDateTime(), nullable=False),
        UniqueConstraint("user_id", "kind", name="user_kind_unique"),
        Index("ix_mfa_codes_expires_at", "expires_at"),
        CheckConstraint("attempts >= 0", name="attempts_nonneg"),
    )


def _oauth_identities(table_name: str) -> Table:
    return Table(
        table_name,
        metadata,
        Column("id", String(36), primary_key=True),
        Column("user_id", String(36), nullable=False),
        Column("provider", String(32), nullable=False),
        Column("subject_id", String(255), nullable=False),
        Column("email", String(320), nullable=True),
        Column("linked_at", UtcDateTime(), nullable=False),
        Column("last_used_at", UtcDateTime(), nullable=True),
        # See OAuthIdentity for the rationale on both unique constraints.
        UniqueConstraint("provider", "subject_id", name="provider_subject_unique"),
        UniqueConstraint("user_id", "provider", name="user_provider_unique"),
    )


def _oauth_states(table_name: str) -> Table:
    return Table(
        table_name,
        metadata,
        # The state-id is the OAuth `state` parameter the browser carries
        # through the provider — random url-safe 32 bytes, supplied by the
        # caller on insert.
        Column("id", String(64), primary_key=True),
        Column("provider", String(32), nullable=False),
        Column("code_verifier", Text, nullable=False),
        Column("nonce", String(64), nullable=False),
        Column("redirect_to", Text, nullable=False),
        Column("mode", String(16), nullable=False),
        Column("linking_user_id", String(36), nullable=True),
        Column("created_at", UtcDateTime(), nullable=False),
        Column("expires_at", UtcDateTime(), nullable=False),
        Column("result_token", Text, nullable=True),
        Index("ix_oauth_states_expires_at", "expires_at"),
        CheckConstraint("mode IN ('signin', 'link')", name="mode_valid"),
    )


# Default-table-name accessors (collection_name from RegStackConfig).
USERS_DEFAULT = "users"
PENDING_DEFAULT = "pending_registrations"
BLACKLIST_DEFAULT = "token_blacklist"
ATTEMPTS_DEFAULT = "login_attempts"
MFA_DEFAULT = "mfa_codes"
OAUTH_IDENTITIES_DEFAULT = "oauth_identities"
OAUTH_STATES_DEFAULT = "oauth_states"

users_table = _users(USERS_DEFAULT)
pending_table = _pending(PENDING_DEFAULT)
blacklist_table = _blacklist(BLACKLIST_DEFAULT)
login_attempts_table = _login_attempts(ATTEMPTS_DEFAULT)
mfa_codes_table = _mfa_codes(MFA_DEFAULT)
oauth_identities_table = _oauth_identities(OAUTH_IDENTITIES_DEFAULT)
oauth_states_table = _oauth_states(OAUTH_STATES_DEFAULT)

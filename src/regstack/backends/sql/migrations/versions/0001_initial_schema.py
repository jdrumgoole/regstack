"""Initial schema — five tables for users, pending registrations,
token blacklist, login attempts, and MFA codes.

Revision ID: 0001
Revises:
Create Date: 2026-04-27

This is the baseline migration: applied to a fresh database it creates
exactly what ``MetaData.create_all`` would have. The point of moving to
Alembic isn't this migration — it's that future schema changes ship as
new revision files that hosts can roll forward without dropping data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from regstack.backends.sql.types import UtcDateTime

revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("phone_number", sa.String(length=20), nullable=True),
        sa.Column("is_mfa_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column("last_login", UtcDateTime(), nullable=True),
        sa.Column("tokens_invalidated_after", UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name="email_unique"),
    )

    op.create_table(
        "pending_registrations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.Text(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_registrations")),
        sa.UniqueConstraint("email", name="pending_email_unique"),
        sa.UniqueConstraint("token_hash", name="pending_token_unique"),
    )
    op.create_index("ix_pending_expires_at", "pending_registrations", ["expires_at"])

    op.create_table(
        "token_blacklist",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("exp", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_token_blacklist")),
        sa.UniqueConstraint("jti", name="jti_unique"),
    )
    op.create_index("ix_blacklist_exp", "token_blacklist", ["exp"])

    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("when", UtcDateTime(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_attempts")),
    )
    op.create_index("ix_login_attempts_email_when", "login_attempts", ["email", "when"])

    op.create_table(
        "mfa_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mfa_codes")),
        sa.UniqueConstraint("user_id", "kind", name="user_kind_unique"),
        sa.CheckConstraint("attempts >= 0", name="ck_mfa_codes_attempts_nonneg"),
    )
    op.create_index("ix_mfa_codes_expires_at", "mfa_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_mfa_codes_expires_at", table_name="mfa_codes")
    op.drop_table("mfa_codes")
    op.drop_index("ix_login_attempts_email_when", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_index("ix_blacklist_exp", table_name="token_blacklist")
    op.drop_table("token_blacklist")
    op.drop_index("ix_pending_expires_at", table_name="pending_registrations")
    op.drop_table("pending_registrations")
    op.drop_table("users")

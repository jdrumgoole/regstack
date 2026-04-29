"""OAuth identities + states; users.hashed_password becomes nullable.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-30

Lands the storage half of OAuth support (per tasks/oauth-design.md):

- ``oauth_identities`` — links a regstack user to one external
  provider identity. Two unique constraints prevent identity-sharing
  and per-user duplicate providers.
- ``oauth_states`` — server-side state row keyed by the random
  ``state`` parameter the browser carries through the provider.
- ``users.hashed_password`` becomes nullable. Existing rows are
  unaffected (they all have values); only new OAuth-only signups
  land with ``NULL``.

The schema change uses ``batch_alter_table`` for SQLite compatibility
— SQLite doesn't support ``ALTER COLUMN`` natively, so Alembic emits
a CREATE-COPY-DROP-RENAME sequence.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from regstack.backends.sql.types import UtcDateTime

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("linked_at", UtcDateTime(), nullable=False),
        sa.Column("last_used_at", UtcDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_identities")),
        sa.UniqueConstraint("provider", "subject_id", name="provider_subject_unique"),
        sa.UniqueConstraint("user_id", "provider", name="user_provider_unique"),
    )

    op.create_table(
        "oauth_states",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("redirect_to", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("linking_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column("expires_at", UtcDateTime(), nullable=False),
        sa.Column("result_token", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_states")),
        sa.CheckConstraint("mode IN ('signin', 'link')", name="ck_oauth_states_mode_valid"),
    )
    op.create_index("ix_oauth_states_expires_at", "oauth_states", ["expires_at"])

    # users.hashed_password becomes nullable. batch_alter_table makes this
    # safe on SQLite (which can't ALTER COLUMN otherwise) and is a no-op
    # rewrite on Postgres.
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "hashed_password",
            existing_type=sa.Text(),
            nullable=True,
        )


def downgrade() -> None:
    # Refuse to roll back a NULL→NOT NULL column if any OAuth-only users
    # exist; otherwise the constraint can't be re-applied.
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "hashed_password",
            existing_type=sa.Text(),
            nullable=False,
        )

    op.drop_index("ix_oauth_states_expires_at", table_name="oauth_states")
    op.drop_table("oauth_states")
    op.drop_table("oauth_identities")

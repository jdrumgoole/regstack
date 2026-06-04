"""Add oauth_states.result_was_new.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-04

The OAuth callback computes whether it created a brand-new account
(vs. signing an existing one in) but had nowhere to persist it, so the
``POST /oauth/exchange`` response always reported
``was_new_account=False``. This adds the column the callback writes and
the exchange endpoint reads (Security review 2026-05-22 · I-1).

The column is NOT NULL with a ``False`` server default so the in-flight
state rows that exist at migration time (if any) get a sensible value
without a backfill. ``batch_alter_table`` keeps the ADD COLUMN safe on
SQLite.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("oauth_states") as batch_op:
        batch_op.add_column(
            sa.Column(
                "result_was_new",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("oauth_states") as batch_op:
        batch_op.drop_column("result_was_new")

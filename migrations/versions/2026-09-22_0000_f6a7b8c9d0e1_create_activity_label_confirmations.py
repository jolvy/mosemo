"""create activity label confirmations

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-22 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_label_confirmations",
        sa.Column("confirmation_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("first_event_id", sa.Uuid(), nullable=False),
        sa.Column("segment_version", sa.String(length=64), nullable=False),
        sa.Column("label_id", sa.Uuid(), nullable=True),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.account_id"],
            name=op.f("activity_label_confirmations_account_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["first_event_id"],
            ["activity_records.event_id"],
            name=op.f("activity_label_confirmations_first_event_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["labels.label_id"],
            name=op.f("activity_label_confirmations_label_id_fkey"),
        ),
        sa.PrimaryKeyConstraint(
            "confirmation_id",
            name=op.f("activity_label_confirmations_pkey"),
        ),
        sa.UniqueConstraint(
            "account_id",
            "first_event_id",
            name=op.f("activity_label_confirmations_account_id_first_event_id_key"),
        ),
    )
    op.create_index(
        op.f("activity_label_confirmations_account_id_idx"),
        "activity_label_confirmations",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("activity_label_confirmations_account_id_idx"),
        table_name="activity_label_confirmations",
    )
    op.drop_table("activity_label_confirmations")

"""create activity label proposals

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-23 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_label_proposals",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("first_event_id", sa.Uuid(), nullable=False),
        sa.Column("segment_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("suggested_label_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("retrieved_example_ids", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("suggested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 3",
            name=op.f("activity_label_proposals_attempt_count_check"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.account_id"],
            name=op.f("activity_label_proposals_account_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["first_event_id"],
            ["activity_records.event_id"],
            name=op.f("activity_label_proposals_first_event_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["suggested_label_id"],
            ["labels.label_id"],
            name=op.f("activity_label_proposals_suggested_label_id_fkey"),
            ondelete="NO ACTION",
        ),
        sa.PrimaryKeyConstraint(
            "proposal_id", name=op.f("activity_label_proposals_pkey")
        ),
        sa.UniqueConstraint(
            "account_id",
            "first_event_id",
            "segment_version",
            name=op.f(
                "activity_label_proposals_account_id_first_event_id_segment_version_key"
            ),
        ),
    )
    op.create_index(
        op.f("activity_label_proposals_account_id_idx"),
        "activity_label_proposals",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("activity_label_proposals_account_id_idx"),
        table_name="activity_label_proposals",
    )
    op.drop_table("activity_label_proposals")

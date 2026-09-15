"""create observation timeline projection

Revision ID: d4e6f7a8b9c0
Revises: a3c9d8e7f6b5
Create Date: 2026-09-16 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e6f7a8b9c0"
down_revision: str | Sequence[str] | None = "a3c9d8e7f6b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_timeline_segments",
        sa.Column("segment_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("segment_type", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_event_id", sa.Uuid(), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text(), none_as_null=True),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "segment_type IN ('activity', 'capture_gap')",
            name=op.f("activity_timeline_segments_segment_type_check"),
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name=op.f("activity_timeline_segments_time_order_check"),
        ),
        sa.CheckConstraint(
            "(segment_type = 'activity' AND last_event_id IS NOT NULL "
            "AND last_observed_at IS NOT NULL AND context IS NOT NULL "
            "AND reason IS NULL) OR (segment_type = 'capture_gap' "
            "AND last_event_id IS NULL AND last_observed_at IS NULL "
            "AND context IS NULL AND reason IS NOT NULL)",
            name=op.f("activity_timeline_segments_kind_fields_check"),
        ),
        sa.CheckConstraint(
            "last_observed_at IS NULL OR "
            "(last_observed_at >= started_at AND "
            "(ended_at IS NULL OR last_observed_at <= ended_at))",
            name=op.f("activity_timeline_segments_last_observed_order_check"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.account_id"],
            name=op.f("activity_timeline_segments_account_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["first_event_id"],
            ["activity_records.event_id"],
            name=op.f("activity_timeline_segments_first_event_id_fkey"),
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["last_event_id"],
            ["activity_records.event_id"],
            name=op.f("activity_timeline_segments_last_event_id_fkey"),
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "segment_id", name=op.f("activity_timeline_segments_pkey")
        ),
    )
    op.create_index(
        op.f("activity_timeline_segments_account_id_started_at_idx"),
        "activity_timeline_segments",
        ["account_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("activity_timeline_segments_account_id_started_at_idx"),
        table_name="activity_timeline_segments",
    )
    op.drop_table("activity_timeline_segments")

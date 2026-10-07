"""Create focus sessions and associate raw activity records.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "focus_sessions",
        sa.Column("session_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.account_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.device_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_seconds", sa.Integer(), nullable=False),
        sa.Column("work_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "label_id",
            sa.Uuid(),
            sa.ForeignKey("labels.label_id", ondelete="NO ACTION"),
            nullable=True,
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.CheckConstraint("target_seconds >= 0", name="target_non_negative"),
        sa.CheckConstraint(
            "work_seconds IS NULL OR work_seconds >= 0", name="work_non_negative"
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="time_order"
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL AND work_seconds IS NULL) OR (ended_at IS NOT NULL AND work_seconds IS NOT NULL AND label_id IS NOT NULL)",
            name="completion_fields",
        ),
    )
    op.create_check_constraint(
        "work_within_duration",
        "focus_sessions",
        "work_seconds IS NULL OR work_seconds <= EXTRACT(EPOCH FROM (ended_at - started_at))",
    )
    op.create_check_constraint(
        "work_within_target",
        "focus_sessions",
        "target_seconds = 0 OR work_seconds IS NULL OR work_seconds <= target_seconds",
    )
    op.create_index(
        "focus_sessions_account_id_started_at_idx",
        "focus_sessions",
        ["account_id", "started_at"],
    )
    op.add_column(
        "activity_records", sa.Column("focus_session_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "activity_records_focus_session_id_fkey",
        "activity_records",
        "focus_sessions",
        ["focus_session_id"],
        ["session_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "activity_records_focus_session_id_idx",
        "activity_records",
        ["focus_session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "activity_records_focus_session_id_idx", table_name="activity_records"
    )
    op.drop_constraint(
        "activity_records_focus_session_id_fkey", "activity_records", type_="foreignkey"
    )
    op.drop_column("activity_records", "focus_session_id")
    op.drop_index(
        "focus_sessions_account_id_started_at_idx", table_name="focus_sessions"
    )
    op.drop_table("focus_sessions")

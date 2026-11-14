"""Store the pauses reported when a focus session completes (M1).

Existing completed sessions get `pauses_reported = false`, meaning no pause
information. The work check allows 2 seconds over the session duration because
the reported work may exceed the analysable time by that much (D-10); the exact
check against the paused time is done by the FocusSession aggregate.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
"""

import sqlalchemy as sa
from alembic import op

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

WORK_WITHIN_DURATION = "focus_sessions_work_within_duration_check"


def upgrade() -> None:
    op.add_column(
        "focus_sessions",
        sa.Column(
            "pauses_reported",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint(
        op.f("focus_sessions_pauses_after_completion_check"),
        "focus_sessions",
        "ended_at IS NOT NULL OR NOT pauses_reported",
    )
    op.drop_constraint(op.f(WORK_WITHIN_DURATION), "focus_sessions", type_="check")
    op.create_check_constraint(
        op.f(WORK_WITHIN_DURATION),
        "focus_sessions",
        "work_seconds IS NULL OR work_seconds <= EXTRACT(EPOCH FROM (ended_at - started_at)) + 2",
    )
    op.create_table(
        "focus_session_pauses",
        sa.Column(
            "focus_session_id",
            sa.Uuid(),
            sa.ForeignKey(
                "focus_sessions.id",
                name=op.f("focus_session_pauses_focus_session_id_fkey"),
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ended_at > started_at",
            name=op.f("focus_session_pauses_time_order_check"),
        ),
        sa.PrimaryKeyConstraint(
            "focus_session_id",
            "started_at",
            name=op.f("focus_session_pauses_pkey"),
        ),
    )


def downgrade() -> None:
    op.drop_table("focus_session_pauses")
    op.drop_constraint(op.f(WORK_WITHIN_DURATION), "focus_sessions", type_="check")
    # Completed sessions passed the stricter check before the upgrade; a session
    # completed afterwards with up to 2 extra seconds fails this downgrade.
    op.create_check_constraint(
        op.f(WORK_WITHIN_DURATION),
        "focus_sessions",
        "work_seconds IS NULL OR work_seconds <= EXTRACT(EPOCH FROM (ended_at - started_at))",
    )
    op.drop_constraint(
        op.f("focus_sessions_pauses_after_completion_check"),
        "focus_sessions",
        type_="check",
    )
    op.drop_column("focus_sessions", "pauses_reported")

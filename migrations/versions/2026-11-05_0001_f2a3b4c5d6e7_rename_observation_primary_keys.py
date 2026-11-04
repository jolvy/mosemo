"""Rename the focus session and timeline segment primary keys to id (ADR 0013).

PostgreSQL keeps the constraints, indexes, and foreign keys that refer to a
renamed column, so only the column names change.
"""

from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("focus_sessions", "session_id", new_column_name="id")
    op.alter_column("activity_timeline_segments", "segment_id", new_column_name="id")


def downgrade() -> None:
    op.alter_column("activity_timeline_segments", "id", new_column_name="segment_id")
    op.alter_column("focus_sessions", "id", new_column_name="session_id")

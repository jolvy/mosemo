"""Rename the label primary key column to id (ADR 0013).

PostgreSQL keeps the constraints, indexes, and foreign keys that refer to a
renamed column, so only the column name changes.
"""

from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("labels", "label_id", new_column_name="id")


def downgrade() -> None:
    op.alter_column("labels", "id", new_column_name="label_id")

"""Rename the identity primary key columns to id (ADR 0013).

PostgreSQL keeps the constraints, indexes, and foreign keys that refer to a
renamed column, so only the column names change.
"""

from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("accounts", "account_id", new_column_name="id")
    op.alter_column("devices", "device_id", new_column_name="id")


def downgrade() -> None:
    op.alter_column("devices", "id", new_column_name="device_id")
    op.alter_column("accounts", "id", new_column_name="account_id")

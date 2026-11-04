"""Rename the label confirmation and proposal primary keys to id (ADR 0013).

No foreign key refers to either column, and PostgreSQL keeps the primary key
constraints on a renamed column, so only the column names change.
"""

from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "activity_label_confirmations", "confirmation_id", new_column_name="id"
    )
    op.alter_column("activity_label_proposals", "proposal_id", new_column_name="id")


def downgrade() -> None:
    op.alter_column("activity_label_proposals", "id", new_column_name="proposal_id")
    op.alter_column(
        "activity_label_confirmations", "id", new_column_name="confirmation_id"
    )

"""Consumer batch evidence and bounded replay ordering index."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activity_batch_receipts",
        sa.Column(
            "account_id",
            sa.Uuid(),
            sa.ForeignKey("accounts.account_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "device_id",
            sa.Uuid(),
            sa.ForeignKey("devices.device_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("batch_id", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("results", postgresql.JSONB(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "activity_records_merge_order_idx",
        "activity_records",
        ["observed_at", "device_id", "sequence", "event_id"],
    )


def downgrade() -> None:
    op.drop_index("activity_records_merge_order_idx", table_name="activity_records")
    op.drop_table("activity_batch_receipts")

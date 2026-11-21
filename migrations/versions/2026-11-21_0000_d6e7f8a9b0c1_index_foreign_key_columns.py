"""Index the foreign key columns that had no index led by them (#343).

Deleting a parent row makes PostgreSQL look up each child table by the foreign
key column (NO ACTION check, CASCADE delete). Without an index led by that
column the lookup is a sequential scan or a skip scan per account. Every index
is a full single-column B-tree, including on nullable columns. There is no
production server yet, so the indexes are built without CONCURRENTLY.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
"""

from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None

FOREIGN_KEY_COLUMNS = (
    ("activity_timeline_segments", "first_event_id"),
    ("activity_timeline_segments", "last_event_id"),
    ("activity_label_confirmations", "first_event_id"),
    ("activity_label_proposals", "first_event_id"),
    ("activity_label_confirmations", "label_id"),
    ("activity_label_proposals", "suggested_label_id"),
    ("focus_sessions", "device_id"),
    ("focus_sessions", "label_id"),
    ("activity_batch_receipts", "device_id"),
)


def upgrade() -> None:
    for table, column in FOREIGN_KEY_COLUMNS:
        op.create_index(op.f(f"{table}_{column}_idx"), table, [column])


def downgrade() -> None:
    for table, column in reversed(FOREIGN_KEY_COLUMNS):
        op.drop_index(op.f(f"{table}_{column}_idx"), table_name=table)

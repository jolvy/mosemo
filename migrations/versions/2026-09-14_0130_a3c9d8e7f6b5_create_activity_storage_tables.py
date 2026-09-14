"""create activity storage tables

Revision ID: a3c9d8e7f6b5
Revises: 7d9c3f1a2b4e
Create Date: 2026-09-14 01:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a3c9d8e7f6b5"
down_revision: str | Sequence[str] | None = "7d9c3f1a2b4e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "device_registrations",
        sa.Column("device_registration_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.account_id"],
            name=op.f("device_registrations_account_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "device_registration_id",
            name=op.f("device_registrations_pkey"),
        ),
    )
    op.create_index(
        op.f("device_registrations_account_id_idx"),
        "device_registrations",
        ["account_id"],
        unique=False,
    )
    op.create_table(
        "activity_records",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("device_registration_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("record_type", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_id", sa.Text(), nullable=False),
        sa.Column("utc_offset_minutes", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "record_type IN ('activity_observation', 'collection_state_changed')",
            name=op.f("activity_records_record_type_check"),
        ),
        sa.CheckConstraint(
            "sequence >= 0",
            name=op.f("activity_records_sequence_non_negative_check"),
        ),
        sa.ForeignKeyConstraint(
            ["device_registration_id"],
            ["device_registrations.device_registration_id"],
            name=op.f("activity_records_device_registration_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("activity_records_pkey")),
        sa.UniqueConstraint(
            "device_registration_id",
            "sequence",
            name=op.f("activity_records_device_registration_id_sequence_key"),
        ),
    )
    op.create_index(
        op.f("activity_records_device_registration_id_observed_at_idx"),
        "activity_records",
        ["device_registration_id", "observed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("activity_records_device_registration_id_observed_at_idx"),
        table_name="activity_records",
    )
    op.drop_table("activity_records")
    op.drop_index(
        op.f("device_registrations_account_id_idx"),
        table_name="device_registrations",
    )
    op.drop_table("device_registrations")

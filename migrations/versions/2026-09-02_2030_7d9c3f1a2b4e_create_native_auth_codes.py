"""create native auth codes table

Revision ID: 7d9c3f1a2b4e
Revises: 44f2c8ca846b
Create Date: 2026-09-02 20:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d9c3f1a2b4e"
down_revision: str | Sequence[str] | None = "44f2c8ca846b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "native_auth_codes",
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("code_challenge", sa.String(length=43), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.account_id"],
            name=op.f("native_auth_codes_account_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "code_digest",
            name=op.f("native_auth_codes_pkey"),
        ),
    )
    op.create_index(
        op.f("native_auth_codes_account_id_idx"),
        "native_auth_codes",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("native_auth_codes_account_id_idx"),
        table_name="native_auth_codes",
    )
    op.drop_table("native_auth_codes")

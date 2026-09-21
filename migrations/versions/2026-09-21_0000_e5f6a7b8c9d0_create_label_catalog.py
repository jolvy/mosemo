"""create label catalog and backfill default labels

Revision ID: e5f6a7b8c9d0
Revises: d4e6f7a8b9c0
Create Date: 2026-09-21 00:00:00

"""

from collections.abc import Sequence
from uuid import uuid7

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_LABEL_VALUES: tuple[tuple[str, str, str], ...] = (
    ("코딩", "코딩", "coding"),
    ("학습", "학습", "learning"),
    ("소통", "소통", "communication"),
    ("쇼핑", "쇼핑", "shopping"),
    ("여가", "여가", "leisure"),
)


def upgrade() -> None:
    op.create_table(
        "labels",
        sa.Column("label_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("name_key", sa.String(length=255), nullable=False),
        sa.Column("default_key", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.account_id"],
            name=op.f("labels_account_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("label_id", name=op.f("labels_pkey")),
        sa.UniqueConstraint(
            "account_id",
            "name_key",
            name=op.f("labels_account_id_name_key_key"),
        ),
        sa.UniqueConstraint(
            "account_id",
            "default_key",
            name=op.f("labels_account_id_default_key_key"),
        ),
    )
    op.create_index(
        op.f("labels_account_id_idx"),
        "labels",
        ["account_id"],
        unique=False,
    )

    backfill_default_labels(op.get_bind())


def backfill_default_labels(connection: Connection) -> None:
    labels_table = sa.table(
        "labels",
        sa.column("label_id", sa.Uuid()),
        sa.column("account_id", sa.Uuid()),
        sa.column("display_name", sa.String(length=255)),
        sa.column("name_key", sa.String(length=255)),
        sa.column("default_key", sa.String(length=64)),
    )
    accounts_table = sa.table(
        "accounts",
        sa.column("account_id", sa.Uuid()),
    )
    account_ids = connection.execute(sa.select(accounts_table.c.account_id)).scalars()
    for account_id in account_ids:
        for display_name, name_key, default_key in DEFAULT_LABEL_VALUES:
            statement = (
                postgresql.insert(labels_table)
                .values(
                    label_id=uuid7(),
                    account_id=account_id,
                    display_name=display_name,
                    name_key=name_key,
                    default_key=default_key,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        labels_table.c.account_id,
                        labels_table.c.default_key,
                    ]
                )
            )
            connection.execute(statement)


def downgrade() -> None:
    op.drop_index(op.f("labels_account_id_idx"), table_name="labels")
    op.drop_table("labels")

from uuid import uuid7

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
)

from mosemo.labels.domain.labels import Label
from mosemo.platform.database.persistence import ORM_METADATA, mapper_registry

labels = Table(
    "labels",
    ORM_METADATA,
    Column("id", Uuid, primary_key=True, default=uuid7),
    Column(
        "account_id",
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("display_name", String(255), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
    Column("archived_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("account_id", "display_name"),
)


def map_tables() -> None:
    """Map the label entity to its table once per process."""
    if Label not in {mapper.class_ for mapper in mapper_registry.mappers}:
        mapper_registry.map_imperatively(Label, labels)

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from mosemo.labeling.domain.confirmations import ActivityLabelConfirmation
from mosemo.labeling.domain.proposals import (
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
)
from mosemo.platform.database.persistence import ORM_METADATA, mapper_registry

activity_label_confirmations = Table(
    "activity_label_confirmations",
    ORM_METADATA,
    Column("id", Uuid, primary_key=True),
    Column(
        "account_id",
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "first_event_id",
        Uuid,
        ForeignKey("activity_records.event_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("segment_version", String(64), nullable=False),
    Column(
        "label_id",
        Uuid,
        ForeignKey("labels.id", ondelete="NO ACTION"),
        nullable=True,
    ),
    Column("confirmed_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("account_id", "first_event_id"),
    Index("activity_label_confirmations_account_id_idx", "account_id"),
)

activity_label_proposals = Table(
    "activity_label_proposals",
    ORM_METADATA,
    Column("id", Uuid, primary_key=True),
    Column(
        "account_id",
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "first_event_id",
        Uuid,
        ForeignKey("activity_records.event_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("segment_version", String(64), nullable=False),
    Column(
        "status",
        Enum(
            ActivityLabelProposalStatus,
            values_callable=lambda members: [member.value for member in members],
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=16,
        ),
        nullable=False,
    ),
    Column(
        "suggested_label_id",
        Uuid,
        ForeignKey("labels.id", ondelete="NO ACTION"),
        nullable=True,
    ),
    Column("attempt_count", Integer, nullable=False),
    Column("lease_token", Uuid, nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("next_attempt_at", DateTime(timezone=True), nullable=True),
    Column("provider", String(64), nullable=True),
    Column("model", Text, nullable=True),
    Column("prompt_version", String(32), nullable=True),
    Column("retrieved_example_ids", JSONB, nullable=True),
    Column("latency_ms", Integer, nullable=True),
    Column("input_tokens", Integer, nullable=True),
    Column("output_tokens", Integer, nullable=True),
    Column("suggested_at", DateTime(timezone=True), nullable=True),
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
    UniqueConstraint("account_id", "first_event_id", "segment_version"),
    CheckConstraint("attempt_count BETWEEN 0 AND 3", name="attempt_count"),
    Index("activity_label_proposals_account_id_idx", "account_id"),
)


def map_tables() -> None:
    """Map the labeling entities to their tables once per process."""
    mapped = {mapper.class_ for mapper in mapper_registry.mappers}
    for entity, table in (
        (ActivityLabelConfirmation, activity_label_confirmations),
        (ActivityLabelProposal, activity_label_proposals),
    ):
        if entity not in mapped:
            mapper_registry.map_imperatively(entity, table)

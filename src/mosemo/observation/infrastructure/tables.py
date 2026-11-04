from enum import StrEnum
from uuid import uuid7

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from mosemo.infra.persistence import ORM_METADATA, TimezoneStorage, mapper_registry
from mosemo.observation.domain.activities import (
    ActivityBatchReceipt,
    ActivityRecord,
    RecordType,
)
from mosemo.observation.domain.focus_sessions import FocusSession
from mosemo.observation.domain.timeline import ActivityTimelineSegment, SegmentType


def _string_enum(enum: type[StrEnum]) -> Enum:
    return Enum(
        enum,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        length=32,
    )


focus_sessions = Table(
    "focus_sessions",
    ORM_METADATA,
    Column("id", Uuid, primary_key=True),
    Column(
        "account_id",
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "device_id",
        Uuid,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=True, default=None),
    Column("target_seconds", Integer, nullable=False),
    Column("work_seconds", Integer, nullable=True, default=None),
    Column(
        "label_id",
        Uuid,
        ForeignKey("labels.id", ondelete="NO ACTION"),
        nullable=True,
        default=None,
    ),
    Column("description", Text, nullable=False, default=""),
    CheckConstraint("target_seconds >= 0", name="target_non_negative"),
    CheckConstraint(
        "work_seconds IS NULL OR work_seconds >= 0", name="work_non_negative"
    ),
    CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="time_order"),
    CheckConstraint(
        "(ended_at IS NULL AND work_seconds IS NULL) OR (ended_at IS NOT NULL AND work_seconds IS NOT NULL AND label_id IS NOT NULL)",
        name="completion_fields",
    ),
    CheckConstraint(
        "work_seconds IS NULL OR work_seconds <= EXTRACT(EPOCH FROM (ended_at - started_at))",
        name="work_within_duration",
    ),
    CheckConstraint(
        "target_seconds = 0 OR work_seconds IS NULL OR work_seconds <= target_seconds",
        name="work_within_target",
    ),
    Index("focus_sessions_account_id_started_at_idx", "account_id", "started_at"),
)

activity_records = Table(
    "activity_records",
    ORM_METADATA,
    Column("event_id", Uuid, primary_key=True),
    Column(
        "device_id",
        Uuid,
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "focus_session_id",
        Uuid,
        ForeignKey("focus_sessions.id", ondelete="RESTRICT"),
        nullable=True,
        default=None,
        index=True,
    ),
    Column("sequence", BigInteger, nullable=False),
    Column("record_type", _string_enum(RecordType), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("timezone_id", TimezoneStorage(Text()), nullable=False),
    Column("utc_offset_minutes", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column(
        "received_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint("device_id", "sequence"),
    Index(
        "activity_records_merge_order_idx",
        "observed_at",
        "device_id",
        "sequence",
        "event_id",
    ),
    CheckConstraint("sequence >= 0", name="sequence_non_negative"),
    CheckConstraint(
        "record_type IN ('activity_observation', 'collection_state_changed')",
        name="record_type",
    ),
    Index(
        "activity_records_device_id_observed_at_idx",
        "device_id",
        "observed_at",
    ),
)

activity_timeline_segments = Table(
    "activity_timeline_segments",
    ORM_METADATA,
    Column("id", Uuid, primary_key=True, default=uuid7),
    Column(
        "account_id",
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("segment_type", _string_enum(SegmentType), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True), nullable=True),
    Column(
        "first_event_id",
        Uuid,
        ForeignKey(
            "activity_records.event_id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    ),
    Column(
        "last_event_id",
        Uuid,
        ForeignKey(
            "activity_records.event_id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    ),
    Column("last_observed_at", DateTime(timezone=True), nullable=True),
    Column("context", JSONB(none_as_null=True), nullable=True),
    Column("reason", Text, nullable=True),
    CheckConstraint(
        "segment_type IN ('activity', 'capture_gap')",
        name="segment_type",
    ),
    CheckConstraint(
        "ended_at IS NULL OR ended_at >= started_at",
        name="time_order",
    ),
    CheckConstraint(
        "(segment_type = 'activity' AND last_event_id IS NOT NULL "
        "AND last_observed_at IS NOT NULL AND context IS NOT NULL "
        "AND reason IS NULL) OR (segment_type = 'capture_gap' "
        "AND last_event_id IS NULL AND last_observed_at IS NULL "
        "AND context IS NULL AND reason IS NOT NULL)",
        name="kind_fields",
    ),
    CheckConstraint(
        "last_observed_at IS NULL OR "
        "(last_observed_at >= started_at AND "
        "(ended_at IS NULL OR last_observed_at <= ended_at))",
        name="last_observed_order",
    ),
    Index(
        "activity_timeline_segments_account_id_started_at_idx",
        "account_id",
        "started_at",
    ),
)

activity_batch_receipts = Table(
    "activity_batch_receipts",
    ORM_METADATA,
    Column(
        "account_id",
        Uuid,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "device_id",
        Uuid,
        ForeignKey("devices.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("batch_id", Uuid, primary_key=True),
    Column("fingerprint", Text, nullable=False),
    Column("results", JSONB, nullable=False),
    Column(
        "processed_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)


def map_tables() -> None:
    """Map the observation entities to their tables once per process."""
    mapped = {mapper.class_ for mapper in mapper_registry.mappers}
    for entity, table in (
        (FocusSession, focus_sessions),
        (ActivityRecord, activity_records),
        (ActivityTimelineSegment, activity_timeline_segments),
        (ActivityBatchReceipt, activity_batch_receipts),
    ):
        if entity not in mapped:
            mapper_registry.map_imperatively(entity, table)

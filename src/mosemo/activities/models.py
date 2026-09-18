from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mosemo.database import Base
from mosemo.timezones import Timezone, TimezoneStorage


class ActivityRecord(Base):
    __tablename__ = "activity_records"
    __table_args__ = (
        UniqueConstraint("device_id", "sequence"),
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

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(BigInteger)
    record_type: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone_id: Mapped[Timezone] = mapped_column(TimezoneStorage(Text()))
    utc_offset_minutes: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ActivityTimelineSegment(Base):
    __tablename__ = "activity_timeline_segments"
    __table_args__ = (
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

    segment_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE")
    )
    segment_type: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_event_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "activity_records.event_id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    last_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "activity_records.event_id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    reason: Mapped[str | None] = mapped_column(Text)

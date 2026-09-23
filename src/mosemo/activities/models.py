from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mosemo.activities.constants import MAX_OBSERVATION_GAP
from mosemo.activities.enums import ActivityTimelineKind, RecordType, SegmentType
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
    record_type: Mapped[RecordType] = mapped_column(
        Enum(
            RecordType,
            values_callable=lambda members: [member.value for member in members],
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=32,
        )
    )
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
    segment_type: Mapped[SegmentType] = mapped_column(
        Enum(
            SegmentType,
            values_callable=lambda members: [member.value for member in members],
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=32,
        )
    )
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

    def effective_ended_at(self, now: datetime) -> datetime | None:
        if self.segment_type is SegmentType.CAPTURE_GAP or self.ended_at is not None:
            return self.ended_at

        assert self.last_observed_at is not None
        if now - self.last_observed_at > MAX_OBSERVATION_GAP:
            return self.last_observed_at
        return None

    def overlaps_window(
        self,
        *,
        start: datetime,
        end: datetime,
        now: datetime,
    ) -> bool:
        ended_at = self.effective_ended_at(now)
        if ended_at is None:
            if self.segment_type is SegmentType.ACTIVITY:
                assert self.last_observed_at is not None
                return self.started_at < end and self.last_observed_at >= start
            return self.started_at < end
        if ended_at == self.started_at:
            return start <= self.started_at < end
        return self.started_at < end and ended_at > start

    def timeline_kind_at(self, now: datetime) -> ActivityTimelineKind:
        if self.segment_type is SegmentType.CAPTURE_GAP:
            return ActivityTimelineKind.CAPTURE_GAP

        assert self.context is not None
        context_kind = self.context.get("kind")
        if context_kind == "opaque":
            return ActivityTimelineKind.OPAQUE_ACTIVITY
        if context_kind != "detailed":
            raise ValueError(f"unsupported activity context kind: {context_kind!r}")
        if self.effective_ended_at(now) is None:
            return ActivityTimelineKind.IN_PROGRESS_ACTIVITY
        return ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY

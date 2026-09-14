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


class DeviceRegistration(Base):
    __tablename__ = "device_registrations"

    device_registration_id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid7,
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
        index=True,
    )


class ActivityRecord(Base):
    __tablename__ = "activity_records"
    __table_args__ = (
        UniqueConstraint("device_registration_id", "sequence"),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
        CheckConstraint(
            "record_type IN ('activity_observation', 'collection_state_changed')",
            name="record_type",
        ),
        Index(
            "activity_records_device_registration_id_observed_at_idx",
            "device_registration_id",
            "observed_at",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    device_registration_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "device_registrations.device_registration_id",
            ondelete="CASCADE",
        )
    )
    sequence: Mapped[int] = mapped_column(BigInteger)
    record_type: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone_id: Mapped[str] = mapped_column(Text)
    utc_offset_minutes: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

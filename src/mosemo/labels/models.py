from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from mosemo.database import Base

DEFAULT_LABEL_NAMES: tuple[str, ...] = ("코딩", "학습", "소통", "쇼핑", "여가")


class Label(Base):
    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("account_id", "display_name"),)

    label_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
    )
    display_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ActivityLabelConfirmation(Base):
    __tablename__ = "activity_label_confirmations"
    __table_args__ = (
        UniqueConstraint("account_id", "first_event_id"),
        Index(
            "activity_label_confirmations_account_id_idx",
            "account_id",
        ),
    )

    confirmation_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
    )
    first_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("activity_records.event_id", ondelete="CASCADE")
    )
    segment_version: Mapped[str] = mapped_column(String(64))
    label_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("labels.label_id", ondelete="NO ACTION"),
        nullable=True,
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )

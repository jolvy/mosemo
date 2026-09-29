from datetime import datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from mosemo.database import Base


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

    def accepts_batch_selection(
        self, *, segment_version: str, label_id: UUID | None
    ) -> bool:
        return self.segment_version != segment_version or self.matches_batch_selection(
            segment_version=segment_version,
            label_id=label_id,
        )

    def matches_batch_selection(
        self, *, segment_version: str, label_id: UUID | None
    ) -> bool:
        return self.segment_version == segment_version and self.label_id == label_id

    def apply_selection(
        self, *, segment_version: str, label_id: UUID | None, now: datetime
    ) -> None:
        if self.segment_version == segment_version and self.label_id == label_id:
            return
        changed_at = max(now, self.updated_at + timedelta(microseconds=1))
        if self.segment_version != segment_version:
            self.segment_version = segment_version
            self.label_id = label_id
            self.confirmed_at = changed_at
            self.updated_at = changed_at
            return
        self.label_id = label_id
        self.updated_at = changed_at

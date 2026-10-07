from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from mosemo.database import Base


class FocusSessionNotFoundError(Exception):
    pass


class FocusSessionConflictError(Exception):
    pass


class FocusSessionInvalidTimeError(Exception):
    pass


class FocusSession(Base):
    __tablename__ = "focus_sessions"
    __table_args__ = (
        CheckConstraint("target_seconds >= 0", name="target_non_negative"),
        CheckConstraint(
            "work_seconds IS NULL OR work_seconds >= 0", name="work_non_negative"
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="time_order"
        ),
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
    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE")
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    target_seconds: Mapped[int] = mapped_column(Integer)
    work_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    label_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("labels.label_id", ondelete="RESTRICT"), default=None
    )
    description: Mapped[str] = mapped_column(Text, default="")

    def complete(
        self,
        *,
        ended_at: datetime,
        work_seconds: int,
        label_id: UUID,
        description: str,
        last_observed_at: datetime | None = None,
    ) -> None:
        if self.ended_at is not None:
            if (self.ended_at, self.work_seconds, self.label_id, self.description) != (
                ended_at,
                work_seconds,
                label_id,
                description,
            ):
                raise FocusSessionConflictError
            return
        if (
            work_seconds < 0
            or ended_at < self.started_at
            or (last_observed_at is not None and ended_at < last_observed_at)
            or work_seconds > (ended_at - self.started_at).total_seconds()
        ):
            raise FocusSessionInvalidTimeError
        if self.target_seconds > 0 and work_seconds > self.target_seconds:
            raise FocusSessionInvalidTimeError
        self.ended_at = ended_at
        self.work_seconds = work_seconds
        self.label_id = label_id
        self.description = description

    def validate_activity(
        self, *, account_id: UUID, device_id: UUID, observed_at: datetime
    ) -> None:
        if self.account_id != account_id or self.device_id != device_id:
            raise FocusSessionNotFoundError
        if observed_at < self.started_at or (
            self.ended_at is not None and observed_at > self.ended_at
        ):
            raise FocusSessionInvalidTimeError

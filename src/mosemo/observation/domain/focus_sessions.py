from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from mosemo.shared.domain.aggregates import AggregateRoot


class FocusSessionNotFoundError(Exception):
    pass


class FocusSessionConflictError(Exception):
    pass


class FocusSessionInvalidTimeError(Exception):
    pass


@dataclass(eq=False, repr=False, kw_only=True)
class FocusSession(AggregateRoot):
    id: UUID
    account_id: UUID
    device_id: UUID
    started_at: datetime
    ended_at: datetime | None = None
    target_seconds: int
    work_seconds: int | None = None
    label_id: UUID | None = None
    description: str = ""

    def has_same_start(self, other: FocusSession) -> bool:
        return (self.device_id, self.started_at, self.target_seconds) == (
            other.device_id,
            other.started_at,
            other.target_seconds,
        )

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

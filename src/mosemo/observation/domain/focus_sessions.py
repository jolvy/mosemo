from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from mosemo.observation.domain.events import FocusSessionCompleted
from mosemo.shared.domain.aggregates import AggregateRoot

# Clients round paused and working time separately, so the reported work may
# exceed the analysable time by this much (D-10).
WORK_SECONDS_TOLERANCE = timedelta(seconds=2)
_SECOND = timedelta(seconds=1)


class FocusSessionNotFoundError(Exception):
    pass


class FocusSessionConflictError(Exception):
    pass


class FocusSessionInvalidTimeError(Exception):
    pass


@dataclass(eq=False, repr=False, kw_only=True)
class FocusSessionPause:
    """A paused span `[started_at, ended_at)` of a completed focus session."""

    started_at: datetime
    ended_at: datetime

    def span(self) -> tuple[datetime, datetime]:
        return (self.started_at, self.ended_at)


@dataclass(frozen=True, slots=True, kw_only=True)
class FocusSessionCompletion:
    """What the client reports when a focus session ends."""

    ended_at: datetime
    work_seconds: int
    label_id: UUID
    description: str
    # `None` means the client sent no pause information.
    pauses: tuple[FocusSessionPause, ...] | None = None


def normalize_pauses(
    pauses: Iterable[FocusSessionPause],
) -> list[FocusSessionPause]:
    """Drop zero-length pauses and sort the rest by start (D9)."""
    return sorted(
        (pause for pause in pauses if pause.started_at != pause.ended_at),
        key=FocusSessionPause.span,
    )


def _pauses_fit(
    pauses: list[FocusSessionPause], *, started_at: datetime, ended_at: datetime
) -> bool:
    """Whether sorted pauses lie inside the session and do not overlap."""
    previous_end = started_at
    for pause in pauses:
        if pause.ended_at < pause.started_at or pause.started_at < previous_end:
            return False
        previous_end = pause.ended_at
    return previous_end <= ended_at


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
    # False means the client sent no pause information, unlike an empty list.
    pauses_reported: bool = False
    pauses: list[FocusSessionPause] = field(default_factory=list)

    def has_same_start(self, other: FocusSession) -> bool:
        return (self.device_id, self.started_at, self.target_seconds) == (
            other.device_id,
            other.started_at,
            other.target_seconds,
        )

    def complete(
        self,
        completion: FocusSessionCompletion,
        *,
        last_observed_at: datetime | None = None,
    ) -> None:
        """Complete once with the normalized pauses.

        An identical retry changes nothing and records no event.
        """
        pauses = (
            None if completion.pauses is None else normalize_pauses(completion.pauses)
        )
        if self.ended_at is not None:
            if not self._has_same_completion(completion, pauses=pauses):
                raise FocusSessionConflictError
            return
        ended_at = completion.ended_at
        work_seconds = completion.work_seconds
        if (
            work_seconds < 0
            or ended_at < self.started_at
            or (last_observed_at is not None and ended_at < last_observed_at)
            or not _pauses_fit(
                pauses or [], started_at=self.started_at, ended_at=ended_at
            )
        ):
            raise FocusSessionInvalidTimeError
        analysable = _analysable_time(self.started_at, ended_at, pauses=pauses or [])
        if work_seconds * _SECOND > analysable + WORK_SECONDS_TOLERANCE:
            raise FocusSessionInvalidTimeError
        if self.target_seconds > 0 and work_seconds > self.target_seconds:
            raise FocusSessionInvalidTimeError
        self.ended_at = ended_at
        self.work_seconds = work_seconds
        self.label_id = completion.label_id
        self.description = completion.description
        self.pauses_reported = pauses is not None
        self.pauses = pauses or []
        self.events.append(
            FocusSessionCompleted(
                account_id=self.account_id,
                focus_session_id=self.id,
                started_at=self.started_at,
                ended_at=ended_at,
            )
        )

    def work_seconds_gap(self) -> int:
        """Whole analysable seconds minus the reported work; negative when over."""
        if self.ended_at is None or self.work_seconds is None:
            raise ValueError("the focus session is not completed")
        analysable = _analysable_time(
            self.started_at, self.ended_at, pauses=self.pauses
        )
        return analysable // _SECOND - self.work_seconds

    def _has_same_completion(
        self,
        completion: FocusSessionCompletion,
        *,
        pauses: list[FocusSessionPause] | None,
    ) -> bool:
        stored_pauses = (
            [pause.span() for pause in self.pauses] if self.pauses_reported else None
        )
        candidate_pauses = None if pauses is None else [p.span() for p in pauses]
        return (
            self.ended_at,
            self.work_seconds,
            self.label_id,
            self.description,
            stored_pauses,
        ) == (
            completion.ended_at,
            completion.work_seconds,
            completion.label_id,
            completion.description,
            candidate_pauses,
        )

    def validate_activity(
        self, *, account_id: UUID, device_id: UUID, observed_at: datetime
    ) -> None:
        if self.account_id != account_id or self.device_id != device_id:
            raise FocusSessionNotFoundError
        if observed_at < self.started_at or (
            self.ended_at is not None and observed_at > self.ended_at
        ):
            raise FocusSessionInvalidTimeError


def _analysable_time(
    started_at: datetime, ended_at: datetime, *, pauses: Iterable[FocusSessionPause]
) -> timedelta:
    # timedelta keeps microseconds exact; float seconds can fall below a whole second.
    paused = sum((pause.ended_at - pause.started_at for pause in pauses), timedelta())
    return ended_at - started_at - paused

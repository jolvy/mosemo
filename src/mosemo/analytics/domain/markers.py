"""Recompute markers: which (account, date) daily stats must be computed again.

ADR 0016 「변경 표시」·「실패·재처리」.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from mosemo.shared.domain.aggregates import AggregateRoot
from mosemo.shared.domain.timezones import Timezone


class MarkerKind(StrEnum):
    SOURCE = "source"
    DEFINITION = "definition"


@dataclass(frozen=True, kw_only=True)
class RetryPolicy:
    """Lease, failure backoff and quarantine threshold of the daily stats batch."""

    lease: timedelta = timedelta(minutes=10)
    backoff: tuple[timedelta, ...] = (
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=2),
        timedelta(hours=12),
        timedelta(hours=24),
    )
    quarantine_threshold: int = 6

    def delay_after(self, attempts: int) -> timedelta:
        """The backoff after the `attempts`-th failed attempt (1-based)."""
        return self.backoff[min(max(attempts, 1), len(self.backoff)) - 1]


RETRY_POLICY = RetryPolicy()


@dataclass(eq=False, repr=False, kw_only=True)
class RecomputeMarker(AggregateRoot):
    """The (account, date) whose stored daily stats are outdated.

    `version` comes from a global sequence each time a change writes the marker;
    the writer also resets `attempts` to 0 then. The batch saves its result as
    `source_version` and deletes the marker only while the version is unchanged.
    """

    account_id: UUID
    local_date: date
    version: int
    kind: MarkerKind
    marked_at: datetime
    eligible_at: datetime
    attempts: int = 0
    next_attempt_at: datetime
    last_error: str | None = None
    last_failed_at: datetime | None = None

    def claim(self, *, now: datetime, policy: RetryPolicy) -> None:
        """Count the attempt and lease the marker so other workers skip it."""
        self.attempts += 1
        self.next_attempt_at = now + policy.lease

    def fail(self, classification: str, *, now: datetime, policy: RetryPolicy) -> None:
        """Back off after a failed attempt and keep only the failure class name."""
        self.next_attempt_at = now + policy.delay_after(self.attempts)
        self.last_error = classification
        self.last_failed_at = now

    def is_quarantined(self, policy: RetryPolicy) -> bool:
        return self.attempts >= policy.quarantine_threshold


class MarkScope:
    """Pure date rules for the markers, in the account timezone."""

    @staticmethod
    def date_of(timezone: Timezone, instant: datetime) -> date:
        return instant.astimezone(ZoneInfo(timezone.value)).date()

    @staticmethod
    def dates_for_range(
        timezone: Timezone, *, start: datetime, end: datetime
    ) -> set[date]:
        """Dates overlapping `[start, end]`, including the date `end` falls on."""
        first = MarkScope.date_of(timezone, start)
        last = MarkScope.date_of(timezone, end)
        return {first + timedelta(days=n) for n in range((last - first).days + 1)}

    @staticmethod
    def eligible_at(local_date: date, timezone: Timezone) -> datetime:
        """One hour after the next local midnight, in UTC."""
        midnight = datetime.combine(
            local_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(timezone.value)
        )
        return midnight.astimezone(UTC) + timedelta(hours=1)

    @staticmethod
    def dates_for_timezone_change(dates: set[date]) -> set[date]:
        """Each date and its neighbours; supported offsets differ by under a day."""
        return {day + timedelta(days=shift) for day in dates for shift in (-1, 0, 1)}

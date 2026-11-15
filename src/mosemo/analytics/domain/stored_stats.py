"""Stored daily stats of one past (account, date) (ADR 0016 「지난 날짜의 일간 통계 저장」)."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from mosemo.analytics.domain.daily_stats import DailyStats
from mosemo.shared.domain.aggregates import AggregateRoot
from mosemo.shared.domain.timezones import Timezone


@dataclass(eq=False, repr=False, kw_only=True)
class StoredDailyStats(AggregateRoot):
    """`source_version` is the marker version the stats were computed for."""

    account_id: UUID
    local_date: date
    source_version: int
    definition_version: int
    timezone: Timezone
    computed_at: datetime
    stats: DailyStats

    def is_current_for(self, definition_version: int, timezone: Timezone) -> bool:
        """Whether a read with this definition and account timezone may use it."""
        return (
            self.definition_version == definition_version and self.timezone == timezone
        )

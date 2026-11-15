"""Where a daily stats read takes its values from (ADR 0016 「분석 조회」, FR-28)."""

from enum import StrEnum

from mosemo.analytics.domain.markers import RecomputeMarker
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.shared.domain.timezones import Timezone


class DailyStatsSource(StrEnum):
    STORED = "stored"
    COMPUTED = "computed"
    FUTURE = "future"

    @staticmethod
    def choose(
        *,
        is_past: bool,
        marker: RecomputeMarker | None,
        stored: StoredDailyStats | None,
        definition_version: int,
        timezone: Timezone,
    ) -> DailyStatsSource:
        """Today, a marked date, a date without a row and a row of another
        definition or timezone are computed; otherwise the stored row is used."""
        if not is_past or marker is not None or stored is None:
            return DailyStatsSource.COMPUTED
        if not stored.is_current_for(definition_version, timezone):
            return DailyStatsSource.COMPUTED
        return DailyStatsSource.STORED

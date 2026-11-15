"""`DailyStatsSource.choose`: when a daily stats read uses the stored row."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from mosemo.analytics.domain.markers import MarkerKind, RecomputeMarker
from mosemo.analytics.domain.stats_source import DailyStatsSource
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.shared.domain.timezones import Timezone
from tests.support.daily_stats import AT, daily_stats

SEOUL = Timezone.ASIA_SEOUL
DAY = date(2026, 11, 1)
NOW = datetime(2026, 11, 15, tzinfo=UTC)


def stored(
    *, definition_version: int = 1, timezone: Timezone = SEOUL
) -> StoredDailyStats:
    return StoredDailyStats(
        account_id=uuid4(),
        local_date=DAY,
        source_version=3,
        definition_version=definition_version,
        timezone=timezone,
        computed_at=AT,
        stats=daily_stats(),
    )


def marker() -> RecomputeMarker:
    return RecomputeMarker(
        account_id=uuid4(),
        local_date=DAY,
        version=4,
        kind=MarkerKind.SOURCE,
        marked_at=NOW,
        eligible_at=NOW,
        next_attempt_at=NOW,
    )


@pytest.mark.parametrize(
    ("is_past", "marked", "row", "expected"),
    [
        pytest.param(False, False, stored(), DailyStatsSource.COMPUTED, id="today"),
        pytest.param(True, True, stored(), DailyStatsSource.COMPUTED, id="marked"),
        pytest.param(True, False, None, DailyStatsSource.COMPUTED, id="no-row"),
        pytest.param(
            True,
            False,
            stored(definition_version=0),
            DailyStatsSource.COMPUTED,
            id="other-definition",
        ),
        pytest.param(
            True,
            False,
            stored(timezone=Timezone.AMERICA_NEW_YORK),
            DailyStatsSource.COMPUTED,
            id="other-timezone",
        ),
        pytest.param(True, False, stored(), DailyStatsSource.STORED, id="stored"),
    ],
)
def test_choose_uses_the_stored_row_only_for_a_current_unmarked_past_date(
    is_past: bool,
    marked: bool,
    row: StoredDailyStats | None,
    expected: DailyStatsSource,
) -> None:
    chosen = DailyStatsSource.choose(
        is_past=is_past,
        marker=marker() if marked else None,
        stored=row,
        definition_version=1,
        timezone=SEOUL,
    )

    assert chosen is expected

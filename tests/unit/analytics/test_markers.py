from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from mosemo.analytics.domain.markers import (
    RETRY_POLICY,
    MarkerKind,
    MarkScope,
    RecomputeMarker,
)
from mosemo.analytics.infrastructure.tables import analytics_daily_markers
from mosemo.shared.domain.timezones import Timezone

SEOUL = Timezone.ASIA_SEOUL
NEW_YORK = Timezone.AMERICA_NEW_YORK
NOW = datetime(2026, 11, 15, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    ("timezone", "start", "end", "expected"),
    [
        # Seoul 2026-11-01 23:30 ~ 11-02 00:40 crosses midnight.
        (
            SEOUL,
            datetime(2026, 11, 1, 14, 30, tzinfo=UTC),
            datetime(2026, 11, 1, 15, 40, tzinfo=UTC),
            {date(2026, 11, 1), date(2026, 11, 2)},
        ),
        # The same UTC range is one New York date.
        (
            NEW_YORK,
            datetime(2026, 11, 1, 14, 30, tzinfo=UTC),
            datetime(2026, 11, 1, 15, 40, tzinfo=UTC),
            {date(2026, 11, 1)},
        ),
        # Ending exactly at Seoul midnight also marks the date that starts there.
        (
            SEOUL,
            datetime(2026, 11, 1, 14, tzinfo=UTC),
            datetime(2026, 11, 1, 15, tzinfo=UTC),
            {date(2026, 11, 1), date(2026, 11, 2)},
        ),
        # A three-day range marks the date in between.
        (
            NEW_YORK,
            datetime(2026, 11, 1, 4, tzinfo=UTC),
            datetime(2026, 11, 4, 6, tzinfo=UTC),
            {date(2026, 11, d) for d in (1, 2, 3, 4)},
        ),
        # A zero-length instant is its own date.
        (
            SEOUL,
            datetime(2026, 11, 1, 15, tzinfo=UTC),
            datetime(2026, 11, 1, 15, tzinfo=UTC),
            {date(2026, 11, 2)},
        ),
    ],
)
def test_dates_for_range(timezone, start, end, expected) -> None:
    assert MarkScope.dates_for_range(timezone, start=start, end=end) == expected


def test_date_of_uses_the_account_timezone() -> None:
    instant = datetime(2026, 11, 1, 15, tzinfo=UTC)
    assert MarkScope.date_of(SEOUL, instant) == date(2026, 11, 2)
    assert MarkScope.date_of(NEW_YORK, instant) == date(2026, 11, 1)


@pytest.mark.parametrize(
    ("timezone", "local_date", "expected"),
    [
        (SEOUL, date(2026, 11, 1), datetime(2026, 11, 1, 16, tzinfo=UTC)),
        (NEW_YORK, date(2026, 3, 7), datetime(2026, 3, 8, 6, tzinfo=UTC)),
        # 2026-03-08 is 23 hours long in New York; the next midnight is EDT.
        (NEW_YORK, date(2026, 3, 8), datetime(2026, 3, 9, 5, tzinfo=UTC)),
        # 2026-11-01 is 25 hours long; the next midnight is EST.
        (NEW_YORK, date(2026, 11, 1), datetime(2026, 11, 2, 6, tzinfo=UTC)),
    ],
)
def test_eligible_at_is_an_hour_after_the_next_local_midnight(
    timezone, local_date, expected
) -> None:
    eligible = MarkScope.eligible_at(local_date, timezone)
    assert eligible == expected
    assert eligible.tzinfo is UTC


def test_dates_for_timezone_change_adds_both_neighbours() -> None:
    assert MarkScope.dates_for_timezone_change(
        {date(2026, 11, 1), date(2026, 11, 5)}
    ) == {date(2026, 11, d) for d in (1, 2, 4, 5, 6)} | {date(2026, 10, 31)}


def marker() -> RecomputeMarker:
    return RecomputeMarker(
        account_id=uuid4(),
        local_date=date(2026, 11, 1),
        version=7,
        kind=MarkerKind.SOURCE,
        marked_at=NOW,
        eligible_at=NOW,
        next_attempt_at=NOW,
    )


def test_claim_counts_the_attempt_and_leases_for_ten_minutes() -> None:
    claimed = marker()
    claimed.claim(now=NOW, policy=RETRY_POLICY)
    assert claimed.attempts == 1
    assert claimed.next_attempt_at == NOW + timedelta(minutes=10)


def test_failures_back_off_and_the_sixth_attempt_is_quarantined() -> None:
    failing = marker()
    delays = []
    for _ in range(6):
        assert not failing.is_quarantined(RETRY_POLICY)
        failing.claim(now=NOW, policy=RETRY_POLICY)
        failing.fail("TimeoutError", now=NOW, policy=RETRY_POLICY)
        delays.append(failing.next_attempt_at - NOW)

    assert delays == [
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=2),
        timedelta(hours=12),
        timedelta(hours=24),
        timedelta(hours=24),
    ]
    assert failing.attempts == 6
    assert failing.is_quarantined(RETRY_POLICY)
    assert failing.last_error == "TimeoutError"
    assert failing.last_failed_at == NOW
    assert failing.version == 7


def test_claim_index_excludes_exactly_the_quarantined_markers() -> None:
    (index,) = analytics_daily_markers.indexes
    predicate = index.dialect_options["postgresql"]["where"]
    assert str(predicate) == f"attempts < {RETRY_POLICY.quarantine_threshold}"

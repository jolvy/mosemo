from datetime import date
from uuid import uuid4

from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.analytics.infrastructure.daily_stats import stats_row, stored_stats
from mosemo.shared.domain.timezones import Timezone
from tests.support.daily_stats import AT, daily_stats


def stored(*, definition_version: int, timezone: Timezone) -> StoredDailyStats:
    return StoredDailyStats(
        account_id=uuid4(),
        local_date=date(2026, 11, 1),
        source_version=3,
        definition_version=definition_version,
        timezone=timezone,
        computed_at=AT,
        stats=daily_stats(),
    )


def test_is_current_only_for_the_same_definition_and_timezone() -> None:
    row = stored(definition_version=1, timezone=Timezone.ASIA_SEOUL)

    assert row.is_current_for(1, Timezone.ASIA_SEOUL)
    assert not row.is_current_for(2, Timezone.ASIA_SEOUL)
    assert not row.is_current_for(1, Timezone.AMERICA_NEW_YORK)


def test_row_round_trips_and_keeps_site_keys_ascii() -> None:
    original = stored(definition_version=1, timezone=Timezone.ASIA_SEOUL)
    row = stats_row(original)

    assert row["target_usage"] == [
        {"kind": "app", "key": "com.example.editor", "us": 6},
        {"kind": "site", "key": "xn--vv4b11d.xn--3e0b707e", "us": 3},
    ]
    assert row["app_usage"] == [
        {"bundleId": "com.example.editor", "name": "Editor", "us": 9}
    ]
    assert row["target_transitions"][0] == {
        "at": "2026-11-01T01:00:00+00:00",
        "prev": {"kind": "app", "key": "com.example.editor"},
        "next": {"kind": "site", "key": "xn--vv4b11d.xn--3e0b707e"},
        "dwellUs": 1,
    }
    assert row["session_distractions"][1] == {
        "kind": "unknown_app",
        "key": None,
        "entries": 1,
        "lapseUs": 1,
    }
    assert row["target_transition_count"] == 2
    assert "short_dwell_transition_count" not in row

    loaded = stored_stats(row)
    assert loaded.stats == original.stats
    assert (loaded.account_id, loaded.source_version, loaded.timezone) == (
        original.account_id,
        3,
        Timezone.ASIA_SEOUL,
    )

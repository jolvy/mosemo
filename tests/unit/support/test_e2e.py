from datetime import UTC, datetime, timedelta

import pytest

from tests.support.e2e import SEOUL, upload_start


def seoul(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 20, hour, minute, second, tzinfo=SEOUL).astimezone(UTC)


def assert_upload_window(now: datetime, start: datetime) -> None:
    assert start + timedelta(minutes=50) <= now
    assert start >= now - timedelta(hours=23)
    end = start + timedelta(minutes=45)
    assert start.astimezone(SEOUL).date() == end.astimezone(SEOUL).date()


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (seoul(12, 30, 15), seoul(11, 30)),
        (seoul(23, 59, 59), seoul(22, 59)),
        (seoul(0, 0), seoul(23, 0) - timedelta(days=1)),
        (seoul(0, 14, 59), seoul(23, 14) - timedelta(days=1)),
        (seoul(0, 15), seoul(22, 0) - timedelta(days=1)),
        (seoul(0, 59, 59), seoul(22, 0) - timedelta(days=1)),
        (seoul(1, 0), seoul(0, 0)),
    ],
    ids=[
        "midday",
        "before_midnight",
        "midnight",
        "last_before_crossing",
        "first_crossing",
        "last_crossing",
        "first_same_day",
    ],
)
def test_upload_start_around_seoul_midnight(now: datetime, expected: datetime) -> None:
    start = upload_start(now)

    assert start == expected
    assert_upload_window(now, start)


def test_upload_start_keeps_every_condition_through_a_day() -> None:
    now = seoul(0, 0)
    while now < seoul(0, 0) + timedelta(days=1):
        assert_upload_window(now, upload_start(now))
        now += timedelta(seconds=37)

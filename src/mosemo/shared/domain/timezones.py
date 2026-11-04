from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class Timezone(StrEnum):
    """IANA timezones accepted by the public API."""

    ASIA_SEOUL = "Asia/Seoul"
    AMERICA_NEW_YORK = "America/New_York"
    UTC = "UTC"


def timeline_date_window(
    *,
    account_timezone: Timezone,
    requested_date: date | None,
    now: datetime,
) -> tuple[date, datetime, datetime] | None:
    timezone = ZoneInfo(account_timezone.value)
    today = now.astimezone(timezone).date()
    selected_date = requested_date or today
    if selected_date > today:
        return None

    try:
        start = datetime.combine(
            selected_date,
            time.min,
            tzinfo=timezone,
        ).astimezone(UTC)
    except OverflowError:
        start = datetime.min.replace(tzinfo=UTC)
    end = datetime.combine(
        selected_date + timedelta(days=1),
        time.min,
        tzinfo=timezone,
    ).astimezone(UTC)
    return selected_date, start, end

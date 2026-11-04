from enum import StrEnum


class Timezone(StrEnum):
    """IANA timezones accepted by the public API."""

    ASIA_SEOUL = "Asia/Seoul"
    AMERICA_NEW_YORK = "America/New_York"
    UTC = "UTC"

from typing import Any, cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import String

from mosemo.timezones import Timezone, TimezoneStorage


def test_timezone_storage_binds_members_and_null() -> None:
    storage = TimezoneStorage(String(64))
    dialect = postgresql.dialect()

    assert storage.process_bind_param(Timezone.ASIA_SEOUL, dialect) == "Asia/Seoul"
    assert storage.process_bind_param(None, dialect) is None


def test_timezone_storage_rejects_unvalidated_strings() -> None:
    with pytest.raises(TypeError, match="timezone must be a Timezone"):
        TimezoneStorage(String(64)).process_bind_param(
            cast(Any, "Asia/Seoul"),
            postgresql.dialect(),
        )

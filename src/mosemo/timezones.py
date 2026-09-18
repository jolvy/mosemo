from enum import StrEnum

from sqlalchemy.engine import Dialect
from sqlalchemy.types import String, TypeDecorator, TypeEngine


class Timezone(StrEnum):
    """IANA timezones accepted by the public API."""

    ASIA_SEOUL = "Asia/Seoul"
    AMERICA_NEW_YORK = "America/New_York"
    UTC = "UTC"


class TimezoneStorage(TypeDecorator[Timezone]):
    impl = String
    cache_ok = True

    def __init__(self, storage_type: TypeEngine[str]) -> None:
        super().__init__()
        self._storage_type = storage_type

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[str]:
        return dialect.type_descriptor(self._storage_type)

    def process_bind_param(
        self, value: Timezone | None, dialect: Dialect
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, Timezone):
            raise TypeError("timezone must be a Timezone")
        return value.value

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> Timezone | None:
        return None if value is None else Timezone(value)

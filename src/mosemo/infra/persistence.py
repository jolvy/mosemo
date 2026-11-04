from sqlalchemy import MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, registry
from sqlalchemy.types import String, TypeDecorator, TypeEngine

from mosemo.shared.domain.timezones import Timezone

POSTGRES_NAMING_CONVENTION = {
    "ix": "%(column_0_label)s_idx",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}

ORM_METADATA = MetaData(
    naming_convention=POSTGRES_NAMING_CONVENTION,
)

# Imperative mappings of moved bounded contexts and the declarative models that
# have not moved yet share one MetaData, so foreign keys between them resolve.
mapper_registry = registry(metadata=ORM_METADATA)


class Base(DeclarativeBase):
    metadata = ORM_METADATA


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

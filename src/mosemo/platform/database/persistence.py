from hashlib import blake2b
from uuid import UUID

from sqlalchemy import MetaData, text
from sqlalchemy.engine import Dialect
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import registry
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

# Every bounded context maps its tables imperatively into this one MetaData,
# so foreign keys between bounded contexts resolve.
mapper_registry = registry(metadata=ORM_METADATA)


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


def account_lock_key(account_id: UUID) -> int:
    """The advisory lock key of one account's source writes (ADR 0016).

    The key bytes must never change: every writer of the account serializes on
    this lock, across deploys too.
    """
    digest = blake2b(
        f"activity_timeline:{account_id}".encode(),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, signed=True)


class AccountLockTimeoutError(Exception):
    """The account advisory lock was not acquired within the lock timeout."""


async def lock_account(db_session: AsyncSession, account_id: UUID) -> None:
    """Hold the account advisory lock until the transaction ends (re-entrant).

    Raises `AccountLockTimeoutError` when it is not acquired within 3 seconds.
    """
    try:
        await db_session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await db_session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": account_lock_key(account_id)},
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise AccountLockTimeoutError from exc
        raise

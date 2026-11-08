"""The seed CLI run against a test database, and reads of what it loaded."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any, cast
from uuid import UUID

from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# Per-account counts of every table the loader writes.
ACCOUNT_COUNTS = {
    "accounts": "SELECT count(*) FROM accounts WHERE id = :a",
    "activity_records": (
        "SELECT count(*) FROM activity_records"
        " WHERE device_id IN (SELECT id FROM devices WHERE account_id = :a)"
    ),
}


@dataclass(frozen=True, slots=True)
class SeedDatabase:
    """Runs the seed CLI against the test database and reads it back."""

    module: ModuleType
    url: URL

    @property
    def host(self) -> str:
        assert self.url.host is not None
        return self.url.host

    def run(self, *args: str) -> int:
        try:
            return self.module.main(list(args))
        except SystemExit as exc:
            return cast(int, exc.code)

    def with_session[T](self, use: Callable[[AsyncSession], Awaitable[T]]) -> T:
        async def run() -> T:
            engine = create_async_engine(self.url)
            try:
                async with AsyncSession(engine, expire_on_commit=False) as session:
                    result = await use(session)
                    await session.commit()
                    return result
            finally:
                await engine.dispose()

        return asyncio.run(run())

    def fetch(self, statement: str, **params: object) -> list[dict[str, Any]]:
        async def query(session: AsyncSession) -> list[dict[str, Any]]:
            result = await session.execute(text(statement), params)
            return [dict(row) for row in result.mappings()]

        return self.with_session(query)

    def execute(self, statement: str, **params: object) -> None:
        async def run(session: AsyncSession) -> None:
            await session.execute(text(statement), params)

        self.with_session(run)

    def counts(self) -> dict[str, int]:
        return {
            table: self.fetch(f"SELECT count(*) AS n FROM {table}")[0]["n"]
            for table in self.module.TABLE_ORDER
        }

    def account_counts(self, account_id: UUID) -> dict[str, int]:
        return {
            table: self.fetch(
                ACCOUNT_COUNTS.get(
                    table, f"SELECT count(*) FROM {table} WHERE account_id = :a"
                ),
                a=account_id,
            )[0]["count"]
            for table in self.module.TABLE_ORDER
        }

    def dummy_account_id(self, provider_subject: str) -> UUID:
        rows = self.fetch(
            "SELECT id FROM accounts WHERE provider_subject = :subject",
            subject=provider_subject,
        )
        assert len(rows) == 1
        return rows[0]["id"]

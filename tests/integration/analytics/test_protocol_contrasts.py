"""TX-13: the rejected protocol variants N1~N6 fail as expected (verification
plan 「PG 두 연결 시나리오」).

Each test replays the shortest counterexample order of one variant with SQL
on separate connections and checks that it yields the expected *wrong*
result. These reproduce the contrasts; they are not regressions of the real
batch. A scratch schema holds a two-part source (the computed value is
`a + b`), the marker and the stored row.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

pytestmark = pytest.mark.asyncio


class Scratch:
    def __init__(self, engine: AsyncEngine, schema: str) -> None:
        self.engine = engine
        self.schema = schema

    async def run(self, *statements: str) -> None:
        """Run the statements in one committed transaction."""
        async with self.engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))

    async def value(self, query: str) -> Any:
        async with self.engine.connect() as connection:
            return await connection.scalar(text(query))

    async def source(self) -> int:
        return int(await self.value("SELECT sum(value) FROM source"))

    async def stored(self) -> Any:
        return await self.value("SELECT value FROM stats")

    async def markers(self) -> int:
        return int(await self.value("SELECT count(*) FROM marker"))

    async def connect(self) -> AsyncConnection:
        connection = await self.engine.connect()
        await connection.execute(text(f"SET search_path TO {self.schema}"))
        await connection.commit()
        return connection


@pytest_asyncio.fixture
async def scratch(integration_database_url: str) -> AsyncIterator[Scratch]:
    schema = f"tx13_{uuid4().hex[:8]}"
    admin = create_async_engine(integration_database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(
        integration_database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    scratch = Scratch(engine, schema)
    await scratch.run(
        "CREATE TABLE source (part text PRIMARY KEY, value int NOT NULL)",
        "INSERT INTO source VALUES ('a', 1), ('b', 10)",
        "CREATE TABLE marker (id int PRIMARY KEY, version bigint, flag boolean)",
        "CREATE TABLE stats (id int PRIMARY KEY, value int NOT NULL,"
        " source_version bigint NOT NULL, definition_version int NOT NULL)",
        "CREATE SEQUENCE versions",
    )
    try:
        yield scratch
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()


async def read(connection: AsyncConnection, query: str) -> Any:
    return await connection.scalar(text(query))


async def test_tx13_n1_boolean_marker_with_unconditional_delete_loses_a_change(
    scratch: Scratch,
) -> None:
    """TX-13 N1: batch reads the marker and part a; the source changes a and
    sets the (boolean) marker again; batch reads b, saves and deletes the
    marker unconditionally. Expected wrong result: no marker, row ≠ source."""
    await scratch.run("INSERT INTO marker (id, flag) VALUES (1, true)")
    batch = await scratch.connect()
    try:
        assert await read(batch, "SELECT flag FROM marker") is True
        a = int(await read(batch, "SELECT value FROM source WHERE part = 'a'"))
        await batch.commit()
        await scratch.run(
            "UPDATE source SET value = 2 WHERE part = 'a'",
            "UPDATE marker SET flag = true",
        )
        b = int(await read(batch, "SELECT value FROM source WHERE part = 'b'"))
        await batch.execute(
            text("INSERT INTO stats VALUES (1, :value, 1, 1)"), {"value": a + b}
        )
        await batch.execute(text("DELETE FROM marker"))
        await batch.commit()
    finally:
        await batch.close()

    assert await scratch.markers() == 0
    assert await scratch.stored() != await scratch.source()


async def test_tx13_n2_save_without_guard_lets_an_old_result_win(
    scratch: Scratch,
) -> None:
    """TX-13 N2: the TX-2 order with an UPSERT that has no guard. Expected wrong
    result: the old value overwrites the new one and no marker is left."""
    await scratch.run(
        "INSERT INTO marker (id, version) VALUES (1, nextval('versions'))"
    )
    first = await scratch.connect()
    try:
        v1 = await read(first, "SELECT version FROM marker")
        old = int(await read(first, "SELECT sum(value) FROM source"))
        await first.commit()
        await scratch.run(
            "UPDATE source SET value = 2 WHERE part = 'a'",
            "UPDATE marker SET version = nextval('versions')",
        )
        v2 = await scratch.value("SELECT version FROM marker")
        await scratch.run(
            f"INSERT INTO stats VALUES (1, (SELECT sum(value) FROM source), {v2}, 1)",
            f"DELETE FROM marker WHERE version = {v2}",
        )
        await first.execute(
            text(
                "INSERT INTO stats VALUES (1, :value, :version, 1) ON CONFLICT (id)"
                " DO UPDATE SET value = EXCLUDED.value,"
                " source_version = EXCLUDED.source_version"
            ),
            {"value": old, "version": v1},
        )
        await first.execute(text("DELETE FROM marker WHERE version = :v"), {"v": v1})
        await first.commit()
    finally:
        await first.close()

    assert await scratch.markers() == 0
    assert await scratch.stored() == old
    assert await scratch.stored() != await scratch.source()


async def test_tx13_n3_reading_the_source_before_the_claim_saves_a_stale_value(
    scratch: Scratch,
) -> None:
    """TX-13 N3: the batch reads the source, the source changes (v2), then the
    batch reads marker v2 and saves and deletes. Expected wrong result: no
    marker and a stale row."""
    batch = await scratch.connect()
    try:
        old = int(await read(batch, "SELECT sum(value) FROM source"))
        await batch.commit()
        await scratch.run(
            "UPDATE source SET value = 2 WHERE part = 'a'",
            "INSERT INTO marker (id, version) VALUES (1, nextval('versions'))",
        )
        v2 = await read(batch, "SELECT version FROM marker")
        await batch.execute(
            text("INSERT INTO stats VALUES (1, :value, :version, 1)"),
            {"value": old, "version": v2},
        )
        await batch.execute(text("DELETE FROM marker WHERE version = :v"), {"v": v2})
        await batch.commit()
    finally:
        await batch.close()

    assert await scratch.markers() == 0
    assert await scratch.stored() != await scratch.source()


async def test_tx13_n4_deleting_the_marker_in_its_own_commit_exposes_a_stale_row(
    scratch: Scratch,
) -> None:
    """TX-13 N4: the batch commits the marker delete before the save. A one
    statement read in between gets no marker and the stale stored row."""
    await scratch.run(
        "INSERT INTO stats VALUES (1, 11, 1, 1)",
        "UPDATE source SET value = 2 WHERE part = 'a'",
        "INSERT INTO marker (id, version) VALUES (1, nextval('versions'))",
    )
    v2 = await scratch.value("SELECT version FROM marker")
    await scratch.run(f"DELETE FROM marker WHERE version = {v2}")

    async with scratch.engine.connect() as reader:
        marker, stored = (
            await reader.execute(
                text(
                    "SELECT m.version, s.value FROM (SELECT 1 AS id) one"
                    " LEFT JOIN marker m USING (id) LEFT JOIN stats s USING (id)"
                )
            )
        ).one()
    await scratch.run(
        f"UPDATE stats SET value = (SELECT sum(value) FROM source), source_version = {v2}"
    )

    assert marker is None
    assert stored == 11
    assert stored != await scratch.source()


async def test_tx13_n5_marker_without_the_lock_and_excluded_version_goes_back(
    scratch: Scratch,
) -> None:
    """TX-13 N5: writer S draws v2 without the account lock; writer M commits
    v3; the batch saves v3; S commits its change with marker v2 (EXCLUDED
    version); batch 2 handles v2. Expected wrong result: the guard blocks the
    save, only the marker is deleted and the row stays stale."""
    writer = await scratch.connect()
    try:
        v2 = await read(writer, "SELECT nextval('versions')")
        await writer.execute(text("UPDATE source SET value = 2 WHERE part = 'a'"))
        await scratch.run(
            "INSERT INTO marker (id, version) VALUES (1, nextval('versions'))"
        )
        v3 = await scratch.value("SELECT version FROM marker")
        assert v3 > v2
        await scratch.run(
            f"INSERT INTO stats SELECT 1, sum(value), {v3}, 1 FROM source",
            f"DELETE FROM marker WHERE version = {v3}",
        )
        await writer.execute(
            text(
                "INSERT INTO marker (id, version) VALUES (1, :v) ON CONFLICT (id)"
                " DO UPDATE SET version = EXCLUDED.version"
            ),
            {"v": v2},
        )
        await writer.commit()
    finally:
        await writer.close()
    await scratch.run(
        "INSERT INTO stats SELECT 1, sum(value), "
        f"{v2}, 1 FROM source ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value,"
        " source_version = EXCLUDED.source_version"
        " WHERE stats.source_version < EXCLUDED.source_version",
        f"DELETE FROM marker WHERE version = {v2}",
    )

    assert await scratch.markers() == 0
    assert await scratch.value("SELECT source_version FROM stats") == v3
    assert await scratch.stored() != await scratch.source()


async def test_tx13_n6_resaving_an_old_definition_row_with_its_source_version_fails(
    scratch: Scratch,
) -> None:
    """TX-13 N6: after definition 1→2 the batch finds the old definition row
    (source_version 3) and saves it again with source_version 3. Expected
    wrong result: `3 < 3` is false, so the old definition row stays."""
    await scratch.run("INSERT INTO stats VALUES (1, 11, 3, 1)")

    await scratch.run(
        "INSERT INTO stats VALUES (1, 99, 3, 2) ON CONFLICT (id) DO UPDATE SET"
        " value = EXCLUDED.value, source_version = EXCLUDED.source_version,"
        " definition_version = EXCLUDED.definition_version"
        " WHERE stats.source_version < EXCLUDED.source_version"
    )

    assert await scratch.value("SELECT definition_version FROM stats") == 1
    assert await scratch.stored() == 11

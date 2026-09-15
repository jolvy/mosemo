import asyncio
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

ACTIVITY_STORAGE_REVISION = "a3c9d8e7f6b5"
PREVIOUS_REVISION = "7d9c3f1a2b4e"


def alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


async def schema_snapshot(database_url: str) -> dict[str, Any]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_inspect_schema)
    finally:
        await engine.dispose()


def _inspect_schema(connection: Any) -> dict[str, Any]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "devices" not in tables:
        return {"tables": tables}

    return {
        "tables": tables,
        "device_columns": {
            column["name"]: column for column in inspector.get_columns("devices")
        },
        "device_pk": inspector.get_pk_constraint("devices"),
        "device_uniques": inspector.get_unique_constraints("devices"),
        "activity_fks": inspector.get_foreign_keys("activity_records"),
        "activity_uniques": inspector.get_unique_constraints("activity_records"),
        "activity_indexes": inspector.get_indexes("activity_records"),
    }


def test_activity_storage_migration_round_trip(
    integration_database_url: str,
) -> None:
    config = alembic_config(integration_database_url)
    assert ScriptDirectory.from_config(config).get_heads() == [
        ACTIVITY_STORAGE_REVISION
    ]

    command.downgrade(config, PREVIOUS_REVISION)
    try:
        downgraded = asyncio.run(schema_snapshot(integration_database_url))
        assert "devices" not in downgraded["tables"]
        assert "activity_records" not in downgraded["tables"]

        command.upgrade(config, "head")
        upgraded = asyncio.run(schema_snapshot(integration_database_url))

        assert {"devices", "activity_records"} <= upgraded["tables"]
        assert "device_registrations" not in upgraded["tables"]
        assert set(upgraded["device_columns"]) == {
            "device_id",
            "account_id",
            "idempotency_key",
        }
        assert upgraded["device_columns"]["idempotency_key"]["nullable"] is False
        assert upgraded["device_pk"]["constrained_columns"] == ["device_id"]
        assert ["account_id", "idempotency_key"] in [
            constraint["column_names"] for constraint in upgraded["device_uniques"]
        ]
        assert any(
            foreign_key["constrained_columns"] == ["device_id"]
            and foreign_key["referred_table"] == "devices"
            and foreign_key["referred_columns"] == ["device_id"]
            and foreign_key["options"].get("ondelete") == "CASCADE"
            for foreign_key in upgraded["activity_fks"]
        )
        assert ["device_id", "sequence"] in [
            constraint["column_names"] for constraint in upgraded["activity_uniques"]
        ]
        assert ["device_id", "observed_at"] in [
            index["column_names"] for index in upgraded["activity_indexes"]
        ]
    finally:
        command.upgrade(config, "head")

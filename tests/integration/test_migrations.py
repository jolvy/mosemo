import asyncio
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

TIMELINE_REVISION = "d4e6f7a8b9c0"
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
        "account_columns": {
            column["name"]: column for column in inspector.get_columns("accounts")
        },
        "device_columns": {
            column["name"]: column for column in inspector.get_columns("devices")
        },
        "device_pk": inspector.get_pk_constraint("devices"),
        "device_uniques": inspector.get_unique_constraints("devices"),
        "activity_fks": inspector.get_foreign_keys("activity_records"),
        "activity_uniques": inspector.get_unique_constraints("activity_records"),
        "activity_indexes": inspector.get_indexes("activity_records"),
        "timeline_columns": {
            column["name"]: column
            for column in inspector.get_columns("activity_timeline_segments")
        },
        "timeline_fks": inspector.get_foreign_keys("activity_timeline_segments"),
        "timeline_checks": inspector.get_check_constraints(
            "activity_timeline_segments"
        ),
    }


def test_activity_storage_migration_round_trip(
    integration_database_url: str,
) -> None:
    config = alembic_config(integration_database_url)
    assert ScriptDirectory.from_config(config).get_heads() == [TIMELINE_REVISION]

    command.downgrade(config, PREVIOUS_REVISION)
    try:
        downgraded = asyncio.run(schema_snapshot(integration_database_url))
        assert "devices" not in downgraded["tables"]
        assert "activity_records" not in downgraded["tables"]
        assert "activity_timeline_segments" not in downgraded["tables"]

        command.upgrade(config, "head")
        upgraded = asyncio.run(schema_snapshot(integration_database_url))

        assert {
            "devices",
            "activity_records",
            "activity_timeline_segments",
        } <= upgraded["tables"]
        assert upgraded["account_columns"]["timezone"]["nullable"] is False
        assert "Asia/Seoul" in upgraded["account_columns"]["timezone"]["default"]
        assert set(upgraded["timeline_columns"]) == {
            "segment_id",
            "account_id",
            "segment_type",
            "started_at",
            "ended_at",
            "first_event_id",
            "last_event_id",
            "last_observed_at",
            "context",
            "reason",
        }
        assert upgraded["timeline_columns"]["first_event_id"]["nullable"] is False
        assert upgraded["timeline_columns"]["last_event_id"]["nullable"] is True
        assert upgraded["timeline_columns"]["last_observed_at"]["nullable"] is True
        assert {
            "activity_timeline_segments_segment_type_check",
            "activity_timeline_segments_time_order_check",
            "activity_timeline_segments_kind_fields_check",
            "activity_timeline_segments_last_observed_order_check",
        } <= {constraint["name"] for constraint in upgraded["timeline_checks"]}
        assert any(
            fk["constrained_columns"] == ["account_id"]
            and fk["options"].get("ondelete") == "CASCADE"
            for fk in upgraded["timeline_fks"]
        )
        assert all(
            fk["options"].get("deferrable") is True
            and fk["options"].get("initially") == "DEFERRED"
            for fk in upgraded["timeline_fks"]
            if fk["constrained_columns"] in (["first_event_id"], ["last_event_id"])
        )
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

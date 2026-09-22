import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

LABEL_REVISION = "e5f6a7b8c9d0"
CONFIRMATION_REVISION = "f6a7b8c9d0e1"
PREVIOUS_REVISION = "d4e6f7a8b9c0"
LABEL_MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations/versions/2026-09-21_0000_e5f6a7b8c9d0_create_label_catalog.py"
)


def load_label_migration() -> Any:
    spec = importlib.util.spec_from_file_location(
        "create_label_catalog",
        LABEL_MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


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

    snapshot = {
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
        "activity_columns": {
            column["name"]: column
            for column in inspector.get_columns("activity_records")
        },
        "activity_checks": inspector.get_check_constraints("activity_records"),
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
    if "labels" in tables:
        snapshot.update(
            {
                "label_columns": {
                    column["name"]: column for column in inspector.get_columns("labels")
                },
                "label_fks": inspector.get_foreign_keys("labels"),
                "label_uniques": inspector.get_unique_constraints("labels"),
            }
        )
    if "activity_label_confirmations" in tables:
        snapshot.update(
            {
                "confirmation_columns": {
                    column["name"]: column
                    for column in inspector.get_columns("activity_label_confirmations")
                },
                "confirmation_fks": inspector.get_foreign_keys(
                    "activity_label_confirmations"
                ),
                "confirmation_uniques": inspector.get_unique_constraints(
                    "activity_label_confirmations"
                ),
                "confirmation_indexes": inspector.get_indexes(
                    "activity_label_confirmations"
                ),
            }
        )
    return snapshot


async def insert_account(database_url: str, account_id: UUID) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO accounts (account_id, provider, provider_subject) "
                    "VALUES (:account_id, 'KAKAO', :provider_subject)"
                ),
                {
                    "account_id": account_id,
                    "provider_subject": f"migration-{account_id}",
                },
            )
    finally:
        await engine.dispose()


async def delete_account(database_url: str, account_id: UUID) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM accounts WHERE account_id = :account_id"),
                {"account_id": account_id},
            )
    finally:
        await engine.dispose()


async def read_labels(database_url: str, account_id: UUID) -> list[tuple[Any, ...]]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT display_name, created_at, updated_at, archived_at "
                    "FROM labels WHERE account_id = :account_id "
                    "ORDER BY display_name"
                ),
                {"account_id": account_id},
            )
            return [tuple(row) for row in result.fetchall()]
    finally:
        await engine.dispose()


async def update_label(
    database_url: str,
    account_id: UUID,
    archived_at: datetime,
    updated_at: datetime,
) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE labels SET archived_at = :archived_at, "
                    "updated_at = :updated_at "
                    "WHERE account_id = :account_id AND display_name = '코딩'"
                ),
                {
                    "account_id": account_id,
                    "archived_at": archived_at,
                    "updated_at": updated_at,
                },
            )
    finally:
        await engine.dispose()


async def rerun_label_backfill(database_url: str) -> None:
    engine = create_async_engine(database_url)
    migration = load_label_migration()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(migration.backfill_default_labels)
    finally:
        await engine.dispose()


def test_schema_migration_round_trip_and_label_backfill(
    integration_database_url: str,
) -> None:
    config = alembic_config(integration_database_url)
    assert ScriptDirectory.from_config(config).get_heads() == [CONFIRMATION_REVISION]
    account_id = uuid4()
    asyncio.run(insert_account(integration_database_url, account_id))

    command.downgrade(config, PREVIOUS_REVISION)
    try:
        downgraded = asyncio.run(schema_snapshot(integration_database_url))
        assert {
            "devices",
            "activity_records",
            "activity_timeline_segments",
        } <= downgraded["tables"]
        assert "labels" not in downgraded["tables"]
        assert "activity_label_confirmations" not in downgraded["tables"]

        command.upgrade(config, "head")
        upgraded = asyncio.run(schema_snapshot(integration_database_url))

        assert {
            "devices",
            "activity_records",
            "activity_timeline_segments",
            "labels",
            "activity_label_confirmations",
        } <= upgraded["tables"]
        assert set(upgraded["label_columns"]) == {
            "label_id",
            "account_id",
            "display_name",
            "created_at",
            "updated_at",
            "archived_at",
        }
        assert "UUID" in str(upgraded["label_columns"]["label_id"]["type"])
        assert upgraded["label_columns"]["label_id"]["nullable"] is False
        assert upgraded["label_columns"]["updated_at"]["nullable"] is False
        assert ["account_id", "display_name"] in [
            constraint["column_names"] for constraint in upgraded["label_uniques"]
        ]
        assert len(upgraded["label_uniques"]) == 1
        assert any(
            foreign_key["constrained_columns"] == ["account_id"]
            and foreign_key["referred_table"] == "accounts"
            and foreign_key["options"].get("ondelete") == "CASCADE"
            for foreign_key in upgraded["label_fks"]
        )
        assert set(upgraded["confirmation_columns"]) == {
            "confirmation_id",
            "account_id",
            "first_event_id",
            "segment_version",
            "label_id",
            "confirmed_at",
            "updated_at",
        }
        assert upgraded["confirmation_columns"]["label_id"]["nullable"] is True
        assert ["account_id", "first_event_id"] in [
            constraint["column_names"]
            for constraint in upgraded["confirmation_uniques"]
        ]
        assert any(
            foreign_key["constrained_columns"] == ["account_id"]
            and foreign_key["referred_table"] == "accounts"
            and foreign_key["options"].get("ondelete") == "CASCADE"
            for foreign_key in upgraded["confirmation_fks"]
        )
        assert any(
            foreign_key["constrained_columns"] == ["first_event_id"]
            and foreign_key["referred_table"] == "activity_records"
            and foreign_key["options"].get("ondelete") == "CASCADE"
            for foreign_key in upgraded["confirmation_fks"]
        )
        assert any(
            foreign_key["constrained_columns"] == ["label_id"]
            and foreign_key["referred_table"] == "labels"
            for foreign_key in upgraded["confirmation_fks"]
        )
        labels_before_rerun = asyncio.run(
            read_labels(integration_database_url, account_id)
        )
        assert sorted(label[0] for label in labels_before_rerun) == sorted(
            ("코딩", "학습", "소통", "쇼핑", "여가")
        )
        assert all(label[1] is not None for label in labels_before_rerun)
        assert all(label[2] is not None for label in labels_before_rerun)
        assert all(label[3] is None for label in labels_before_rerun)
        archived_at = datetime(2030, 1, 1, tzinfo=UTC)
        updated_at = datetime(2031, 1, 1, tzinfo=UTC)
        asyncio.run(
            update_label(
                integration_database_url,
                account_id,
                archived_at=archived_at,
                updated_at=updated_at,
            )
        )
        asyncio.run(rerun_label_backfill(integration_database_url))
        labels_after_rerun = asyncio.run(
            read_labels(integration_database_url, account_id)
        )
        assert len(labels_after_rerun) == 5
        coding_label = next(label for label in labels_after_rerun if label[0] == "코딩")
        original_coding = next(
            label for label in labels_before_rerun if label[0] == "코딩"
        )
        assert coding_label[1] == original_coding[1]
        assert coding_label[2:] == (updated_at, archived_at)
        assert upgraded["account_columns"]["timezone"]["nullable"] is False
        assert str(upgraded["account_columns"]["timezone"]["type"]) == "VARCHAR(255)"
        assert "Asia/Seoul" in upgraded["account_columns"]["timezone"]["default"]
        assert str(upgraded["activity_columns"]["record_type"]["type"]) == "VARCHAR(32)"
        assert str(upgraded["activity_columns"]["timezone_id"]["type"]) == "TEXT"
        assert (
            str(upgraded["timeline_columns"]["segment_type"]["type"]) == "VARCHAR(32)"
        )
        assert "activity_records_record_type_check" in {
            constraint["name"] for constraint in upgraded["activity_checks"]
        }
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
        asyncio.run(delete_account(integration_database_url, account_id))

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

LABEL_REVISION = "e5f6a7b8c9d0"
PROPOSAL_REVISION = "a7b8c9d0e1f2"
PREVIOUS_REVISION = "d4e6f7a8b9c0"
RECEIPT_REVISION = "c9d0e1f2a3b4"
IDENTITY_KEY_REVISION = "d0e1f2a3b4c5"
LABEL_KEY_REVISION = "e1f2a3b4c5d6"
OBSERVATION_KEY_REVISION = "f2a3b4c5d6e7"
LABELING_KEY_REVISION = "a3b4c5d6e7f8"
# Drift that already existed at c9d0e1f2a3b4; any other difference fails.
ALLOWED_METADATA_DRIFT = [
    ("modify_type", "activity_records", "timezone_id"),
    ("remove_constraint", "accounts_account_provider_check"),
]
LABEL_MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "migrations/versions/2026-09-21_0000_e5f6a7b8c9d0_create_label_catalog.py"
)


def load_label_migration() -> Any:
    spec = importlib.util.spec_from_file_location(
        "create_label_catalog",
        LABEL_MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
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
    if "activity_label_proposals" in tables:
        snapshot.update(
            {
                "proposal_columns": {
                    column["name"]: column
                    for column in inspector.get_columns("activity_label_proposals")
                },
                "proposal_checks": inspector.get_check_constraints(
                    "activity_label_proposals"
                ),
                "proposal_fks": inspector.get_foreign_keys("activity_label_proposals"),
                "proposal_uniques": inspector.get_unique_constraints(
                    "activity_label_proposals"
                ),
            }
        )
    return snapshot


def test_fresh_receipt_schema_has_no_source(integration_database_url: str) -> None:
    async def receipt_schema():
        engine = create_async_engine(integration_database_url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync: (
                        inspect(sync).get_columns("activity_batch_receipts"),
                        inspect(sync).get_pk_constraint("activity_batch_receipts"),
                    )
                )
        finally:
            await engine.dispose()

    columns, primary_key = asyncio.run(receipt_schema())
    assert {column["name"] for column in columns} == {
        "account_id",
        "device_id",
        "batch_id",
        "fingerprint",
        "results",
        "processed_at",
    }
    assert primary_key["constrained_columns"] == ["account_id", "device_id", "batch_id"]


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
    assert ScriptDirectory.from_config(config).get_heads() == [LABELING_KEY_REVISION]
    # The assertions below describe the schema up to the batch receipts revision.
    command.downgrade(config, RECEIPT_REVISION)
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
        assert "activity_label_proposals" not in downgraded["tables"]
        assert "activity_batch_receipts" not in downgraded["tables"]

        command.upgrade(config, RECEIPT_REVISION)
        upgraded = asyncio.run(schema_snapshot(integration_database_url))

        assert {
            "devices",
            "activity_records",
            "activity_timeline_segments",
            "labels",
            "activity_label_confirmations",
            "activity_label_proposals",
            "activity_batch_receipts",
        } <= upgraded["tables"]
        assert set(upgraded["proposal_columns"]) == {
            "proposal_id",
            "account_id",
            "first_event_id",
            "segment_version",
            "status",
            "suggested_label_id",
            "attempt_count",
            "lease_token",
            "lease_expires_at",
            "next_attempt_at",
            "provider",
            "model",
            "prompt_version",
            "retrieved_example_ids",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "suggested_at",
            "created_at",
            "updated_at",
        }
        assert str(upgraded["proposal_columns"]["status"]["type"]) == "VARCHAR(16)"
        assert {check["name"] for check in upgraded["proposal_checks"]} == {
            "activity_label_proposals_attempt_count_check"
        }
        assert ["account_id", "first_event_id", "segment_version"] in [
            constraint["column_names"] for constraint in upgraded["proposal_uniques"]
        ]
        assert any(
            fk["constrained_columns"] == ["suggested_label_id"]
            and fk["referred_table"] == "labels"
            and fk["options"].get("ondelete") != "CASCADE"
            for fk in upgraded["proposal_fks"]
        )
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
        command.upgrade(config, RECEIPT_REVISION)
        asyncio.run(delete_account(integration_database_url, account_id))
        command.upgrade(config, "head")


def test_focus_session_migration_preserves_segments_without_session_foreign_key(
    integration_database_url: str,
) -> None:
    config = alembic_config(integration_database_url)
    command.downgrade(config, PROPOSAL_REVISION)
    try:
        previous = asyncio.run(schema_snapshot(integration_database_url))
        assert "focus_sessions" not in previous["tables"]
        assert "focus_session_id" not in previous["activity_columns"]
    finally:
        command.upgrade(config, "head")
    upgraded = asyncio.run(schema_snapshot(integration_database_url))
    assert "focus_sessions" in upgraded["tables"]
    assert upgraded["activity_columns"]["focus_session_id"]["nullable"]
    assert any(
        fk["constrained_columns"] == ["focus_session_id"]
        and fk["referred_table"] == "focus_sessions"
        for fk in upgraded["activity_fks"]
    )

    async def segment_columns():
        engine = create_async_engine(integration_database_url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync: {
                        column["name"]
                        for column in inspect(sync).get_columns(
                            "activity_timeline_segments"
                        )
                    }
                )
        finally:
            await engine.dispose()

    assert "focus_session_id" not in asyncio.run(segment_columns())


async def identity_key_columns(database_url: str) -> dict[str, Any]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_inspect_identity_keys)
    finally:
        await engine.dispose()


def _inspect_identity_keys(connection: Any) -> dict[str, Any]:
    inspector = inspect(connection)
    return {
        "account_pk": inspector.get_pk_constraint("accounts")["constrained_columns"],
        "device_pk": inspector.get_pk_constraint("devices")["constrained_columns"],
        "referred": sorted(
            (table, foreign_key["referred_table"], *foreign_key["referred_columns"])
            for table in inspector.get_table_names()
            for foreign_key in inspector.get_foreign_keys(table)
            if foreign_key["referred_table"] in {"accounts", "devices"}
        ),
    }


def test_identity_primary_key_migration_renames_columns_and_keeps_foreign_keys(
    integration_database_url: str,
) -> None:
    config = alembic_config(integration_database_url)
    command.downgrade(config, RECEIPT_REVISION)
    try:
        before = asyncio.run(identity_key_columns(integration_database_url))
    finally:
        command.upgrade(config, "head")
    after = asyncio.run(identity_key_columns(integration_database_url))

    assert before["account_pk"] == ["account_id"]
    assert before["device_pk"] == ["device_id"]
    assert after["account_pk"] == ["id"]
    assert after["device_pk"] == ["id"]
    # Every foreign key still points at the renamed primary keys.
    assert [row[:2] for row in after["referred"]] == [
        row[:2] for row in before["referred"]
    ]
    assert {row[2] for row in before["referred"]} == {"account_id", "device_id"}
    assert {row[2] for row in after["referred"]} == {"id"}
    assert len([row for row in after["referred"] if row[1] == "accounts"]) == 8
    assert len([row for row in after["referred"] if row[1] == "devices"]) == 3


async def label_key_columns(database_url: str) -> dict[str, Any]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_inspect_label_keys)
    finally:
        await engine.dispose()


def _inspect_label_keys(connection: Any) -> dict[str, Any]:
    inspector = inspect(connection)
    return {
        "label_pk": inspector.get_pk_constraint("labels")["constrained_columns"],
        "referred": sorted(
            (table, foreign_key["referred_table"], *foreign_key["referred_columns"])
            for table in inspector.get_table_names()
            for foreign_key in inspector.get_foreign_keys(table)
            if foreign_key["referred_table"] == "labels"
        ),
    }


def test_label_primary_key_migration_renames_column_and_keeps_foreign_keys(
    integration_database_url: str,
) -> None:
    config = alembic_config(integration_database_url)
    command.downgrade(config, IDENTITY_KEY_REVISION)
    try:
        before = asyncio.run(label_key_columns(integration_database_url))
    finally:
        command.upgrade(config, "head")
    after = asyncio.run(label_key_columns(integration_database_url))

    assert before["label_pk"] == ["label_id"]
    assert after["label_pk"] == ["id"]
    # Every foreign key still points at the renamed primary key.
    assert [row[:2] for row in after["referred"]] == [
        row[:2] for row in before["referred"]
    ]
    assert {row[2] for row in before["referred"]} == {"label_id"}
    assert {row[2] for row in after["referred"]} == {"id"}
    assert len(after["referred"]) == 3


async def observation_key_columns(database_url: str) -> dict[str, Any]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_inspect_observation_keys)
    finally:
        await engine.dispose()


def _inspect_observation_keys(connection: Any) -> dict[str, Any]:
    inspector = inspect(connection)
    tables = {"focus_sessions", "activity_timeline_segments"}
    return {
        "focus_session_pk": inspector.get_pk_constraint("focus_sessions")[
            "constrained_columns"
        ],
        "segment_pk": inspector.get_pk_constraint("activity_timeline_segments")[
            "constrained_columns"
        ],
        "referred": sorted(
            (table, foreign_key["referred_table"], *foreign_key["referred_columns"])
            for table in inspector.get_table_names()
            for foreign_key in inspector.get_foreign_keys(table)
            if foreign_key["referred_table"] in tables
        ),
    }


def test_observation_primary_key_migration_renames_columns_and_keeps_foreign_keys(
    integration_database_url: str,
) -> None:
    config = alembic_config(integration_database_url)
    command.downgrade(config, LABEL_KEY_REVISION)
    try:
        before = asyncio.run(observation_key_columns(integration_database_url))
    finally:
        command.upgrade(config, "head")
    after = asyncio.run(observation_key_columns(integration_database_url))

    assert before["focus_session_pk"] == ["session_id"]
    assert before["segment_pk"] == ["segment_id"]
    assert after["focus_session_pk"] == ["id"]
    assert after["segment_pk"] == ["id"]
    # The one foreign key to a renamed primary key still points at it.
    assert before["referred"] == [("activity_records", "focus_sessions", "session_id")]
    assert after["referred"] == [("activity_records", "focus_sessions", "id")]


async def labeling_key_columns(database_url: str) -> dict[str, Any]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_inspect_labeling_keys)
    finally:
        await engine.dispose()


def _inspect_labeling_keys(connection: Any) -> dict[str, Any]:
    inspector = inspect(connection)
    tables = {"activity_label_confirmations", "activity_label_proposals"}
    return {
        "pks": {
            table: inspector.get_pk_constraint(table)["constrained_columns"]
            for table in sorted(tables)
        },
        "referred": [
            table
            for table in inspector.get_table_names()
            for foreign_key in inspector.get_foreign_keys(table)
            if foreign_key["referred_table"] in tables
        ],
    }


def test_labeling_primary_key_migration_renames_columns(
    integration_database_url: str,
) -> None:
    config = alembic_config(integration_database_url)
    command.downgrade(config, OBSERVATION_KEY_REVISION)
    try:
        before = asyncio.run(labeling_key_columns(integration_database_url))
    finally:
        command.upgrade(config, "head")
    after = asyncio.run(labeling_key_columns(integration_database_url))

    assert before["pks"] == {
        "activity_label_confirmations": ["confirmation_id"],
        "activity_label_proposals": ["proposal_id"],
    }
    assert after["pks"] == {
        "activity_label_confirmations": ["id"],
        "activity_label_proposals": ["id"],
    }
    # No foreign key refers to either primary key.
    assert before["referred"] == after["referred"] == []


def _drift_key(operation: tuple[Any, ...]) -> tuple[str, ...]:
    if operation[0].startswith("modify_"):
        return (operation[0], operation[2], operation[3])
    return (operation[0], str(getattr(operation[1], "name", operation[1])))


async def metadata_drift(database_url: str) -> list[tuple[str, ...]]:
    # Same mapping functions as migrations/env.py.
    from mosemo.identity import bootstrap as identity_bootstrap
    from mosemo.identity.infrastructure import tables as identity_tables
    from mosemo.labeling import bootstrap as labeling_bootstrap
    from mosemo.labeling.infrastructure import tables as labeling_tables
    from mosemo.labels import bootstrap as labels_bootstrap
    from mosemo.labels.infrastructure import tables as labels_tables
    from mosemo.observation import bootstrap as observation_bootstrap
    from mosemo.observation.infrastructure import tables as observation_tables
    from mosemo.platform.database.persistence import ORM_METADATA

    identity_bootstrap.map_tables()
    labels_bootstrap.map_tables()
    observation_bootstrap.map_tables()
    labeling_bootstrap.map_tables()
    assert {
        identity_tables.accounts,
        observation_tables.activity_records,
        labels_tables.labels,
        labeling_tables.activity_label_confirmations,
        labeling_tables.activity_label_proposals,
        identity_tables.native_auth_codes,
        identity_tables.devices,
        observation_tables.focus_sessions,
    } <= set(ORM_METADATA.tables.values())

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            diffs = await connection.run_sync(
                lambda sync: compare_metadata(
                    MigrationContext.configure(sync), ORM_METADATA
                )
            )
    finally:
        await engine.dispose()
    operations = [
        operation
        for diff in diffs
        for operation in (diff if isinstance(diff, list) else [diff])
    ]
    return sorted(_drift_key(operation) for operation in operations)


def test_head_schema_matches_shared_metadata_except_known_drift(
    integration_database_url: str,
) -> None:
    drift = asyncio.run(metadata_drift(integration_database_url))

    assert drift == ALLOWED_METADATA_DRIFT

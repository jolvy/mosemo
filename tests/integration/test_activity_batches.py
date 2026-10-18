import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.activities.batch_processing import batch_fingerprint
from mosemo.activities.models import ActivityBatchReceipt, ActivityRecord
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import ActivityBatchRequest
from mosemo.activities.timeline import ProjectedActivity, project_events
from mosemo.auth.tokens import TokenService
from mosemo.config import Config
from mosemo.devices.models import Device


def record(sequence, seconds, *, context=None, event_id=None):
    observed = datetime(2026, 9, 14, tzinfo=UTC) + timedelta(seconds=seconds)
    return {
        "eventId": str(event_id or uuid4()),
        "sequence": sequence,
        "recordType": "activity_observation",
        "observedAt": observed.isoformat().replace("+00:00", "Z"),
        "timezoneId": "Asia/Seoul",
        "utcOffsetMinutes": 540,
        "context": context or {"kind": "opaque"},
    }


def batch(device, records, *, batch_id=None):
    return {
        "batchId": str(batch_id or uuid4()),
        "deviceId": str(device),
        "records": records,
    }


@pytest_asyncio.fixture
async def real_batch_app(
    integration_database_url, config: Config, monkeypatch
) -> AsyncIterator:
    # Exercise the direct-processing route against isolated PostgreSQL. Kafka
    # publication is tested independently; Consumer execution belongs to #65.
    from mosemo import database
    from mosemo.main import create_app

    app = create_app()
    app.state.batch_runtime.config = app.state.batch_runtime.config.model_copy(
        update={"enabled": False}
    )

    engine = create_async_engine(integration_database_url)
    closed_sessions = []

    class ObservedSession(AsyncSession):
        async def close(self):
            closed_sessions.append(self)
            await super().close()

    factory = async_sessionmaker(engine, class_=ObservedSession, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionFactory", factory)
    # Main token config is the explicit non-secret test environment, overridden
    # with the fixture config for matching signing claims.
    from mosemo.config import get_config

    app.dependency_overrides[get_config] = lambda: config
    accounts = []
    devices = []
    async with factory() as session:
        for _ in range(2):
            account = AccountRepository(session).save(
                provider=AccountProvider.KAKAO, provider_subject=f"batches-{uuid4()}"
            )
            await session.flush()
            device = Device(account_id=account.account_id, idempotency_key=uuid4())
            session.add(device)
            await session.flush()
            accounts.append(account.account_id)
            devices.append(device.device_id)
        await session.commit()
    closed_sessions.clear()
    try:
        async with (
            LifespanManager(app) as manager,
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(
                    app=manager.app, raise_app_exceptions=False
                ),
                base_url="http://test",
            ) as client,
        ):
            headers = [
                {
                    "Authorization": "Bearer "
                    + TokenService(config.auth).issue_access_token(owner)
                }
                for owner in accounts
            ]
            yield app, client, factory, accounts, devices, headers, closed_sessions
    finally:
        app.dependency_overrides.clear()
        async with factory() as session:
            await session.execute(
                delete(Account).where(Account.account_id.in_(accounts))
            )
            await session.commit()
        await engine.dispose()


async def counts(factory, device):
    async with factory() as session:
        return tuple(
            [
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.device_id == device)
                )
                for model in (ActivityRecord, ActivityBatchReceipt)
            ]
        )


@pytest.mark.asyncio
async def test_batch_winners_duplicates_gaps_lower_sequence_and_scoped_receipts(
    real_batch_app,
):
    _app, client, factory, accounts, devices, headers, _ = real_batch_app
    first, later = record(10, 10), record(90, 90)
    conflicting = record(10, 15)
    payload = batch(devices[0], [first, first, conflicting, later])
    response = await client.post(
        "/api/v1/activities/batches/legacy", headers=headers[0], json=payload
    )
    assert response.status_code == 200
    async with factory() as session:
        receipt = await session.get(
            ActivityBatchReceipt, (accounts[0], devices[0], UUID(payload["batchId"]))
        )
        assert [r["status"] for r in receipt.results] == [
            "stored",
            "duplicate",
            "sequence_rejected",
            "stored",
        ]
        assert receipt.fingerprint == batch_fingerprint(
            ActivityBatchRequest.model_validate(payload)
        )
        assert receipt.processed_at is not None
        assert await session.get(ActivityRecord, UUID(conflicting["eventId"])) is None
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[0], json=payload
        )
    ).status_code == 200
    assert await counts(factory, devices[0]) == (2, 1)
    lower = batch(devices[0], [record(0, 0), record(30, 30)])
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[0], json=lower
        )
    ).status_code == 200
    assert await counts(factory, devices[0]) == (4, 2)
    foreign = batch(
        devices[1], [record(0, 0, event_id=UUID(first["eventId"])), record(1, 1)]
    )
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[1], json=foreign
        )
    ).status_code == 200
    async with factory() as session:
        receipt = await session.get(
            ActivityBatchReceipt, (accounts[1], devices[1], UUID(foreign["batchId"]))
        )
        assert [r["status"] for r in receipt.results] == ["event_id_rejected", "stored"]
    changed = payload | {"records": [record(100, 100)]}
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[0], json=changed
        )
    ).status_code == 409
    assert await counts(factory, devices[0]) == (4, 2)


@pytest.mark.asyncio
async def test_postgres_projection_failure_rolls_back_whole_batch_and_other_account_continues(
    real_batch_app, monkeypatch
):
    _app, client, factory, _, devices, headers, _ = real_batch_app
    original = ActivityRepository.replace_account_segments

    async def fail_after_flush(self, **kwargs):
        await original(self, **kwargs)
        if kwargs["account_id"] == real_batch_app[3][0]:
            await self._session.execute(text("SELECT 1/0"))

    monkeypatch.setattr(
        ActivityRepository, "replace_account_segments", fail_after_flush
    )
    payload = batch(devices[0], [record(0, 0), record(20, 20)])
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[0], json=payload
        )
    ).status_code == 500
    assert await counts(factory, devices[0]) == (0, 0)
    async with factory() as session:
        assert (
            await ActivityRepository(session).list_account_segments(
                real_batch_app[3][0]
            )
            == []
        )
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy",
            headers=headers[1],
            json=batch(devices[1], [record(0, 0)]),
        )
    ).status_code == 200
    assert await counts(factory, devices[1]) == (1, 1)


@pytest.mark.asyncio
async def test_append_and_past_replay_match_full_reference_and_stop_reading_suffix(
    real_batch_app, monkeypatch
):
    _app, client, factory, accounts, devices, headers, _ = real_batch_app

    # Many changes exceed one page. Every point has distinct context, making
    # an unchanged closed segment a safe early convergence boundary.
    def context(index):
        return {
            "kind": "detailed",
            "app": {
                "bundleId": {"status": "absent"},
                "name": {"status": "captured", "value": str(index)},
            },
            "window": {"status": "absent"},
            "web": {"kind": "not_applicable"},
        }

    for start in range(0, 400, 200):
        payload = batch(
            devices[0],
            [record(i * 10, i, context=context(i)) for i in range(start, start + 200)],
        )
        assert (
            await client.post(
                "/api/v1/activities/batches/legacy", headers=headers[0], json=payload
            )
        ).status_code == 200
    pages = []
    original = ActivityRepository.record_page

    async def counted(self, *args, **kwargs):
        page = await original(self, *args, **kwargs)
        pages.append(len(page))
        return page

    monkeypatch.setattr(ActivityRepository, "record_page", counted)
    appended = batch(devices[0], [record(4000, 400, context=context(400))])
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[0], json=appended
        )
    ).status_code == 200
    assert pages == []
    async with factory() as session:
        before = await ActivityRepository(session).list_account_segments(accounts[0])
    late = batch(
        devices[0],
        [
            record(105, 10.5, context=context("late")),
            record(106, 10.6, context=context("late")),
        ],
    )
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[0], json=late
        )
    ).status_code == 200
    assert pages == [128]
    async with factory() as session:
        repo = ActivityRepository(session)
        source = await repo.list_account_records(
            accounts[0], from_observed_at=datetime(2026, 9, 14, tzinfo=UTC)
        )
        segments = await repo.list_account_segments(accounts[0])
    reference = project_events(source, now=datetime.now(UTC))
    for stored, expected in zip(segments, reference, strict=True):
        assert (stored.started_at, stored.ended_at, stored.first_event_id) == (
            expected.started_at,
            expected.ended_at,
            expected.first_event_id,
        )
        assert isinstance(expected, ProjectedActivity)
        assert stored.last_observed_at == expected.last_observed_at
        assert stored.last_event_id == expected.last_event_id
        assert stored.context == expected.context
    unchanged = {
        s.first_event_id: s.segment_id for s in before if s.started_at.second > 15
    }
    assert all(
        s.segment_id == unchanged[s.first_event_id]
        for s in segments
        if s.first_event_id in unchanged
    )


@pytest.mark.asyncio
async def test_regressing_clock_rolls_back_batch(real_batch_app):
    _, client, factory, _, devices, headers, _ = real_batch_app
    source = batch(devices[0], [record(10, 10), record(11, 5)])
    response = await client.post(
        "/api/v1/activities/batches/legacy", headers=headers[0], json=source
    )
    assert response.status_code == 422
    assert await counts(factory, devices[0]) == (0, 0)


@pytest.mark.asyncio
async def test_two_http_process_runtimes_reread_concurrent_event_id_winner(
    real_batch_app, config, monkeypatch
):
    from mosemo.config import get_config
    from mosemo.main import create_app

    _app, client, factory, accounts, devices, headers, _ = real_batch_app
    second = create_app()
    second.state.batch_runtime.config = second.state.batch_runtime.config.model_copy(
        update={"enabled": False}
    )
    second.dependency_overrides[get_config] = lambda: config
    entered = asyncio.Event()
    release = asyncio.Event()
    arrivals = 0
    original = ActivityRepository.insert
    shared_event = uuid4()

    async def simultaneous(self, source):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            entered.set()
        await release.wait()
        return await original(self, source)

    monkeypatch.setattr(ActivityRepository, "insert", simultaneous)
    first_source = batch(devices[0], [record(0, 0, event_id=shared_event)])
    second_source = batch(devices[1], [record(0, 1, event_id=shared_event)])
    async with (
        LifespanManager(second) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app, raise_app_exceptions=False),
            base_url="http://test",
        ) as other,
    ):
        requests = asyncio.gather(
            client.post(
                "/api/v1/activities/batches/legacy",
                headers=headers[0],
                json=first_source,
            ),
            other.post(
                "/api/v1/activities/batches/legacy",
                headers=headers[1],
                json=second_source,
            ),
        )
        await asyncio.wait_for(entered.wait(), 3)
        release.set()
        responses = await asyncio.wait_for(requests, 5)
        assert [response.status_code for response in responses] == [200, 200]
    async with factory() as session:
        receipts = [
            await session.get(
                ActivityBatchReceipt, (owner, device, UUID(source["batchId"]))
            )
            for owner, device, source in zip(
                accounts, devices, [first_source, second_source], strict=True
            )
        ]
        assert sorted(receipt.results[0]["status"] for receipt in receipts) == [
            "event_id_rejected",
            "stored",
        ]
        winner = await session.get(ActivityRecord, shared_event)
        assert winner is not None
        for receipt in receipts:
            if receipt.results[0]["status"] == "stored":
                assert receipt.device_id == winner.device_id
            else:
                assert receipt.device_id != winner.device_id


@pytest.mark.asyncio
@pytest.mark.parametrize("first_batch", ["append", "late", "duplicate", "rollback"])
async def test_first_receipt_rebuilds_legacy_tie_order_before_incremental_processing(
    real_batch_app, monkeypatch, first_batch
):
    _app, client, factory, accounts, devices, headers, _ = real_batch_app

    def context(name):
        return {
            "kind": "detailed",
            "app": {
                "bundleId": {"status": "absent"},
                "name": {"status": "captured", "value": name},
            },
            "window": {"status": "absent"},
            "web": {"kind": "not_applicable"},
        }

    legacy = ActivityBatchRequest.model_validate(
        batch(
            devices[0],
            [
                record(10, 0, context=context("B")),
                record(0, 0, context=context("A")),
                record(20, 10, context=context("A")),
                record(30, 100, context=context("C")),
                record(50, 200, context=context("F")),
                record(40, 200, context=context("E")),
                record(60, 210, context=context("E")),
            ],
        )
    )
    async with factory() as session:
        repo = ActivityRepository(session)
        stored: list[ActivityRecord] = []
        for source in legacy.activity_records():
            inserted = await repo.insert(source)
            assert inserted is not None
            stored.append(inserted)
        # Legacy ties followed received_at: B before A, and F before E.
        await repo.replace_account_segments(
            account_id=accounts[0],
            projected=project_events(stored, now=datetime.now(UTC)),
            existing=[],
            event_observed_at=stored[-1].observed_at,
        )
        await session.commit()
        original = await repo.list_account_segments(accounts[0])
        unchanged_id = next(s.segment_id for s in original if s.context == context("C"))

    def shape(segment):
        return (
            segment.started_at,
            segment.ended_at,
            segment.first_event_id,
            segment.last_event_id,
            segment.last_observed_at,
            segment.context,
        )

    async def assert_matches_full_replay():
        async with factory() as session:
            repo = ActivityRepository(session)
            source = await repo.list_account_records(
                accounts[0], from_observed_at=stored[0].observed_at
            )
            segments = await repo.list_account_segments(accounts[0])
        reference = project_events(source, now=datetime.now(UTC))
        assert [shape(s) for s in segments] == [shape(s) for s in reference]
        assert (
            next(s.segment_id for s in segments if s.context == context("C"))
            == unchanged_id
        )

    first = {
        "append": record(70, 220, context=context("E")),
        "late": record(15, 5, context=context("A")),
        "duplicate": legacy.records[0].model_dump(mode="json", by_alias=True),
        "rollback": record(70, 220, context=context("E")),
    }[first_batch]
    payload = batch(devices[0], [first])
    original_insert = ActivityRepository.insert
    if first_batch == "rollback":

        async def fail_insert(self, source):
            raise RuntimeError("controlled failure after legacy rebuild")

        monkeypatch.setattr(ActivityRepository, "insert", fail_insert)
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[0], json=payload
        )
    ).status_code == (500 if first_batch == "rollback" else 200)
    if first_batch == "rollback":
        assert await counts(factory, devices[0]) == (len(stored), 0)
        async with factory() as session:
            rolled_back = await ActivityRepository(session).list_account_segments(
                accounts[0]
            )
        assert [(s.segment_id, shape(s)) for s in rolled_back] == [
            (s.segment_id, shape(s)) for s in original
        ]
        monkeypatch.setattr(ActivityRepository, "insert", original_insert)
        response = await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[0], json=payload
        )
        assert response.status_code == 200
    assert await counts(factory, devices[0]) == (
        len(stored) + (first_batch != "duplicate"),
        1,
    )
    await assert_matches_full_replay()

    pages = []
    original_page = ActivityRepository.record_page

    async def counted(self, *args, **kwargs):
        pages.append(kwargs)
        return await original_page(self, *args, **kwargs)

    monkeypatch.setattr(ActivityRepository, "record_page", counted)
    for following in [
        payload,
        batch(devices[0], [record(80, 230, context=context("E"))]),
    ]:
        assert (
            await client.post(
                "/api/v1/activities/batches/legacy", headers=headers[0], json=following
            )
        ).status_code == 200
        assert pages == []
    await assert_matches_full_replay()


@pytest.mark.asyncio
async def test_first_receipt_detects_legacy_clock_regression_without_rewriting_raw(
    real_batch_app,
):
    from mosemo.activities.enums import RecordType
    from mosemo.timezones import Timezone

    _app, client, factory, _, devices, headers, _ = real_batch_app
    async with factory() as session:
        for sequence, second in [(0, 10), (1, 5)]:
            session.add(
                ActivityRecord(
                    event_id=uuid4(),
                    device_id=devices[0],
                    sequence=sequence,
                    record_type=RecordType.ACTIVITY_OBSERVATION,
                    observed_at=datetime(2026, 9, 14, 0, 0, second, tzinfo=UTC),
                    timezone_id=Timezone.ASIA_SEOUL,
                    utc_offset_minutes=540,
                    payload={"context": {"kind": "opaque"}},
                )
            )
        await session.commit()
    payload = batch(devices[0], [record(2, 20)])
    assert (
        await client.post(
            "/api/v1/activities/batches/legacy", headers=headers[0], json=payload
        )
    ).status_code == 422
    assert await counts(factory, devices[0]) == (2, 0)


@pytest.mark.asyncio
async def test_legacy_commits_projection_retries_and_rolls_back_invalid_batch(
    real_batch_app,
):
    _app, client, factory, accounts, devices, headers, _ = real_batch_app
    path = "/api/v1/activities/batches/legacy"
    source = batch(devices[0], [record(0, 0), record(1, 10)])
    response = await client.post(path, headers=headers[0], json=source)
    assert response.status_code == 200
    assert [item["status"] for item in response.json()["results"]] == [
        "stored",
        "stored",
    ]
    assert await counts(factory, devices[0]) == (2, 1)
    async with factory() as session:
        segments = await ActivityRepository(session).segment_range(
            accounts[0], start=datetime.min.replace(tzinfo=UTC)
        )
        assert segments
    retry = await client.post(path, headers=headers[0], json=source)
    assert retry.json() == response.json()
    assert await counts(factory, devices[0]) == (2, 1)
    changed = source | {"records": [record(2, 20)]}
    assert (
        await client.post(path, headers=headers[0], json=changed)
    ).status_code == 409
    assert (await client.post(path, headers=headers[1], json=source)).status_code == 404
    invalid = batch(devices[0], [record(2, 20), record(3, 15)])
    assert (
        await client.post(path, headers=headers[0], json=invalid)
    ).status_code == 422
    assert await counts(factory, devices[0]) == (2, 1)


@pytest.mark.asyncio
async def test_normalized_retry_and_conflict_preserve_receipt_and_timeline(
    real_batch_app,
):
    _, client, factory, accounts, devices, headers, _ = real_batch_app
    path = "/api/v1/activities/batches/legacy"
    payload = batch(devices[0], [record(0, 0), record(1, 10)])

    async def snapshot():
        async with factory() as session:
            receipt = await session.get(
                ActivityBatchReceipt,
                (accounts[0], devices[0], UUID(payload["batchId"])),
            )
            segments = await ActivityRepository(session).list_account_segments(
                accounts[0]
            )
            return (
                receipt.fingerprint,
                receipt.results,
                receipt.processed_at,
                [(s.segment_id, s.started_at, s.ended_at) for s in segments],
            )

    first = await client.post(path, headers=headers[0], json=payload)
    assert first.status_code == 200
    original = await snapshot()
    # Different wire bytes and equivalent validated timestamps have the same hash.
    normalized = payload | {
        "records": [
            item | {"observedAt": item["observedAt"].replace("Z", ".000000Z")}
            for item in payload["records"]
        ]
    }
    retry = await client.post(
        path,
        headers=headers[0] | {"Content-Type": "application/json"},
        content=json.dumps(normalized, indent=2, sort_keys=True),
    )
    assert retry.json() == first.json()
    assert await snapshot() == original
    for changed in [
        payload | {"records": list(reversed(payload["records"]))},
        payload | {"records": [record(2, 20)]},
    ]:
        conflict = await client.post(path, headers=headers[0], json=changed)
        assert conflict.status_code == 409
        assert await snapshot() == original
    assert await counts(factory, devices[0]) == (2, 1)

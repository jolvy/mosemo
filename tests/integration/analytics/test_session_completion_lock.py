"""Session completion waits for the account lock the analytics marking takes."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mosemo.analytics.infrastructure.tables import analytics_daily_markers
from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.entrypoints.api.routes import v1_api_router
from mosemo.entrypoints.consumer.activity_batches import register_analytics_handlers
from mosemo.identity.bootstrap import IdentityConfig, create_device_directory
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.devices import Device
from mosemo.labels.bootstrap import create_label_catalog
from mosemo.labels.domain.labels import Label
from mosemo.observation.application.batches import ProcessActivityBatch
from mosemo.observation.application.focus_sessions import (
    CompleteFocusSession,
    FocusSessionLinks,
)
from mosemo.observation.domain.activities import ActivityRecord
from mosemo.observation.domain.focus_sessions import (
    FocusSession,
    FocusSessionCompletion,
)
from mosemo.observation.infrastructure.activity_records import (
    ActivityRecordRepository,
)
from mosemo.observation.infrastructure.focus_sessions import FocusSessionRepository
from mosemo.observation.infrastructure.tables import activity_records, focus_sessions
from mosemo.observation.presentation.activities.schemas import ActivityBatchRequest
from mosemo.observation.presentation.messages import ActivityRegistrationRequested
from mosemo.platform.clock import utc_now
from mosemo.platform.database.engine import get_session
from mosemo.platform.database.persistence import account_lock_key
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.presentation.current_account import get_current_account
from tests.support.batches import batch_client
from tests.support.identity import (
    current_account_provider,
    delete_accounts,
    token_service,
)
from tests.support.observation import batch, record

STARTED_AT = datetime(2026, 10, 7, tzinfo=UTC)


@pytest.mark.asyncio
async def test_completion_is_busy_while_another_connection_holds_the_lock(
    integration_database_url: str, identity_config: IdentityConfig
) -> None:
    engine = create_async_engine(integration_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_current_account] = current_account_provider(
        identity_config
    )

    async def session_override() -> AsyncIterator:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    async with factory() as session:
        account = Account(provider=AccountProvider.KAKAO, provider_subject=str(uuid4()))
        session.add(account)
        await session.flush()
        device = Device(account_id=account.id, idempotency_key=uuid4())
        label = Label(account_id=account.id, display_name="집중")
        focus = FocusSession(
            id=uuid4(),
            account_id=account.id,
            device_id=device.id,
            started_at=STARTED_AT,
            target_seconds=0,
        )
        session.add_all([device, label])
        await session.flush()
        session.add(focus)
        await session.commit()
    headers = {
        "Authorization": "Bearer "
        + token_service(identity_config).issue_access_token(account.id, now=utc_now())
    }
    url = f"/api/v1/focus-sessions/{focus.id}/completion"
    body = {
        "endedAt": "2026-10-07T00:20:00Z",
        "workSeconds": 1100,
        "labelId": str(label.id),
        "description": "",
    }
    try:
        async with batch_client(app, factory) as client:
            async with factory() as holder, holder.begin():
                await holder.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": account_lock_key(account.id)},
                )
                waiting = asyncio.create_task(
                    client.put(url, headers=headers, json=body)
                )
                await asyncio.sleep(0.5)
                # The waiting completion holds no session row lock yet: the
                # account lock comes first, as in activity recording.
                async with factory() as probe, probe.begin():
                    await probe.execute(
                        select(focus_sessions.c.id)
                        .where(focus_sessions.c.id == focus.id)
                        .with_for_update(nowait=True)
                    )
                busy = await waiting

            assert busy.status_code == 503
            assert busy.json()["error"]["status"] == "ACTIVITY_TIMELINE_BUSY"
            assert busy.headers["Retry-After"] == "1"

            retried = await client.put(url, headers=headers, json=body)
            assert retried.status_code == 200, retried.text

        async with factory() as session:
            dates = (
                await session.scalars(
                    select(analytics_daily_markers.c.local_date).where(
                        analytics_daily_markers.c.account_id == account.id
                    )
                )
            ).all()
        # The account is in Seoul by default.
        assert dates == [date(2026, 10, 7)]
    finally:
        async with factory() as session:
            await session.execute(
                delete(FocusSession).where(focus_sessions.c.account_id == account.id)
            )
            await session.execute(delete_accounts(account.id))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_consumer_batch_and_completion_take_the_account_lock_first(
    integration_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(integration_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        account = Account(provider=AccountProvider.KAKAO, provider_subject=str(uuid4()))
        session.add(account)
        await session.flush()
        device = Device(account_id=account.id, idempotency_key=uuid4())
        label = Label(account_id=account.id, display_name="집중")
        # `record` observes at 2026-09-14T00:00:00Z.
        focus = FocusSession(
            id=uuid4(),
            account_id=account.id,
            device_id=device.id,
            started_at=datetime(2026, 9, 13, 23, 50, tzinfo=UTC),
            target_seconds=0,
        )
        session.add_all([device, label])
        await session.flush()
        session.add(focus)
        await session.commit()
    linked = {**record(0, 0), "focusSessionId": str(focus.id)}
    activity_batch = ActivityRegistrationRequested.from_batch(
        account.id, ActivityBatchRequest.model_validate(batch(device.id, [linked]))
    ).to_activity_batch()
    dispatcher = EventDispatcher()
    register_analytics_handlers(dispatcher)
    process_batch = ProcessActivityBatch(
        devices=create_device_directory(), dispatcher=dispatcher, clock=utc_now
    )
    complete = CompleteFocusSession(
        focus_sessions=FocusSessionRepository(),
        activity_records=ActivityRecordRepository(),
        labels=create_label_catalog(),
        dispatcher=dispatcher,
    )
    completion = FocusSessionCompletion(
        ended_at=datetime(2026, 9, 14, 0, 20, tzinfo=UTC),
        work_seconds=1800,
        label_id=label.id,
        description="",
    )
    validate = FocusSessionLinks.validate
    paused = asyncio.Event()
    release = asyncio.Event()

    async def held(self, *args, **kwargs):
        await validate(self, *args, **kwargs)
        if not paused.is_set():
            # The batch now holds the session row lock.
            paused.set()
            await release.wait()

    monkeypatch.setattr(FocusSessionLinks, "validate", held)

    async def run_batch():
        async with factory() as session:
            return await process_batch.execute(
                session, account_id=account.id, batch=activity_batch
            )

    async def run_completion():
        async with factory() as session:
            return await complete.execute(
                session,
                account_id=account.id,
                focus_session_id=focus.id,
                completion=completion,
            )

    consumer = asyncio.create_task(run_batch())
    completing = None
    key = account_lock_key(account.id)
    try:
        await asyncio.wait_for(paused.wait(), 3)
        completing = asyncio.create_task(run_completion())
        # The completion waits for the account lock the batch took first, not
        # for the session row lock.
        async with asyncio.timeout(2), factory() as observer:
            while not await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                    "AND classid::bigint = :high AND objid::bigint = :low "
                    "AND NOT granted)"
                ),
                {"high": (key >> 32) & 0xFFFFFFFF, "low": key & 0xFFFFFFFF},
            ):
                await asyncio.sleep(0.01)
        release.set()
        results = await consumer
        completed = await completing

        assert results == [{"eventId": linked["eventId"], "status": "stored"}]
        assert completed.ended_at == completion.ended_at
        async with factory() as session:
            ended_at = await session.scalar(
                select(focus_sessions.c.ended_at).where(focus_sessions.c.id == focus.id)
            )
        assert ended_at == completion.ended_at
    finally:
        release.set()
        await asyncio.gather(
            consumer, *([completing] if completing else []), return_exceptions=True
        )
        async with factory() as session:
            await session.execute(
                delete(ActivityRecord).where(activity_records.c.device_id == device.id)
            )
            await session.execute(
                delete(FocusSession).where(focus_sessions.c.account_id == account.id)
            )
            await session.execute(delete_accounts(account.id))
            await session.commit()
        await engine.dispose()

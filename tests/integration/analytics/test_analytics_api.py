"""Daily stats and focus session analysis over HTTP (Issue #286, ADR 0016 「분석 조회」)."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mosemo.analytics.bootstrap import Analytics, public_suffix_rules
from mosemo.analytics.domain.daily_stats import DailyStatsCalculator
from mosemo.analytics.domain.definition import CURRENT_DEFINITION
from mosemo.analytics.domain.source import DayWindow
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.analytics.domain.usage_targets import UsageTargetResolver
from mosemo.analytics.infrastructure.daily_stats import DailyStatsRepository
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.analytics.infrastructure.tables import (
    analytics_daily_markers,
    analytics_daily_stats,
)
from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.entrypoints.api.routes import v1_api_router
from mosemo.identity.bootstrap import IdentityConfig, create_account_directory
from mosemo.labeling.bootstrap import create_label_confirmation_directory
from mosemo.labeling.infrastructure.tables import activity_label_confirmations
from mosemo.observation.application.timeline import ObservationTimeline
from mosemo.observation.bootstrap import (
    create_focus_session_log,
    create_observation_timeline,
)
from mosemo.observation.infrastructure.tables import (
    activity_timeline_segments,
    focus_sessions,
)
from mosemo.platform.clock import utc_now
from mosemo.platform.database.engine import get_session
from mosemo.shared.domain.timezones import Timezone
from mosemo.shared.presentation.current_account import get_current_account
from tests.support.analytics_sources import (
    OPAQUE,
    SourceSeed,
    at,
    delete_seeds,
    detailed,
)
from tests.support.clock import FakeClock
from tests.support.daily_stats import daily_stats
from tests.support.identity import current_account_provider, token_service

SEOUL = Timezone.ASIA_SEOUL
DAY = date(2026, 11, 3)
NEXT_DAY = date(2026, 11, 4)
# 2026-11-10 00:00 Seoul: 11-03 is a past date.
NOW = at(7 * 1440)
EDITOR = detailed("com.example.editor", name="Editor")
NOTES = detailed("com.example.notes", name="Notes")
YOUTUBE = detailed(
    "com.google.Chrome", name="Chrome", url="https://m.youtube.com/watch?v=1"
)
KOREAN_SITE = detailed("com.google.Chrome", name="Chrome", url="https://예시.한국/")
READS_LOGGER = "mosemo.analytics.application.reads"
TIMING_FIELDS = (
    "source",
    "source_read_ms",
    "snapshot_build_ms",
    "compute_ms",
    "segment_count",
)


def iso(minutes: float) -> str:
    return at(minutes).isoformat().replace("+00:00", "Z")


def minutes_us(value: float) -> int:
    return int(value * 60_000_000)


@dataclass
class AnalyticsApi:
    client: httpx2.AsyncClient
    factory: async_sessionmaker[AsyncSession]
    clock: FakeClock
    analytics: Analytics
    seed: SourceSeed
    other: SourceSeed
    headers: dict[str, str]
    other_headers: dict[str, str]
    session_override: Callable[[], AsyncIterator[AsyncSession]]

    @asynccontextmanager
    async def seeding(self) -> AsyncIterator[SourceSeed]:
        """Add rows for the main account (and `other`) in one committed session."""
        async with self.factory() as session:
            self.seed.session = session
            self.other.session = session
            yield self.seed
            await self.seed.flush()
            await self.other.flush()
            await session.commit()

    async def daily(self, day: date | str, **kwargs: Any) -> httpx2.Response:
        return await self.client.get(
            f"/api/v1/analytics/daily-stats?date={day}",
            headers=kwargs.get("headers", self.headers),
        )

    async def session_analysis(
        self, focus_session_id: UUID, *, headers: dict[str, str] | None = None
    ) -> httpx2.Response:
        return await self.client.get(
            f"/api/v1/analytics/focus-sessions/{focus_session_id}",
            headers=headers or self.headers,
        )

    async def save_current_stats(self, day: date, *, source_version: int) -> None:
        """Store what the batch would: the date computed from the source now."""
        async with self.factory() as session:
            window = DayWindow.of(SEOUL, day)
            loaded = await self.analytics.source_loader.load_day(
                session,
                self.seed.account_id,
                timezone=SEOUL,
                window=window,
                now=self.clock(),
            )
            stats = DailyStatsCalculator(
                UsageTargetResolver(public_suffix_rules())
            ).compute(loaded.snapshot, window)
            await DailyStatsRepository().save_if_newer(
                session,
                StoredDailyStats(
                    account_id=self.seed.account_id,
                    local_date=day,
                    source_version=source_version,
                    definition_version=CURRENT_DEFINITION.version,
                    timezone=SEOUL,
                    computed_at=self.clock(),
                    stats=stats,
                ),
            )
            await session.commit()

    async def save_stale_stats(self, day: date) -> None:
        """A current-definition row whose values no longer match the source."""
        async with self.factory() as session:
            await DailyStatsRepository().save_if_newer(
                session,
                StoredDailyStats(
                    account_id=self.seed.account_id,
                    local_date=day,
                    source_version=1,
                    definition_version=CURRENT_DEFINITION.version,
                    timezone=SEOUL,
                    computed_at=self.clock(),
                    stats=daily_stats(),
                ),
            )
            await session.commit()

    async def mark(self, day: date) -> None:
        async with self.factory() as session:
            await RecomputeMarkerRepository().upsert_source(
                session,
                account_id=self.seed.account_id,
                local_date=day,
                eligible_at=self.clock(),
                now=self.clock(),
            )
            await session.commit()


def bearer(config: IdentityConfig, account_id: UUID) -> dict[str, str]:
    token = token_service(config).issue_access_token(account_id, now=utc_now())
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def api(
    integration_database_url: str, identity_config: IdentityConfig
) -> AsyncIterator[AnalyticsApi]:
    engine = create_async_engine(integration_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clock = FakeClock(NOW)
    analytics = Analytics(
        accounts=create_account_directory(),
        timeline=create_observation_timeline(),
        focus_sessions=create_focus_session_log(),
        confirmations=create_label_confirmation_directory(),
        clock=clock,
    )
    app = FastAPI()
    register_exception_handlers(app)
    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(analytics.router(), prefix="/analytics")
    app.include_router(v1)
    app.dependency_overrides[get_current_account] = current_account_provider(
        identity_config
    )

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    async with factory() as session:
        seed = SourceSeed(session)
        await seed.create_account()
        other = SourceSeed(session)
        await other.create_account()
        await session.commit()
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield AnalyticsApi(
            client=client,
            factory=factory,
            clock=clock,
            analytics=analytics,
            seed=seed,
            other=other,
            headers=bearer(identity_config, seed.account_id),
            other_headers=bearer(identity_config, other.account_id),
            session_override=session_override,
        )
    async with factory() as session:
        await delete_seeds(session, seed.account_id, other.account_id)
    await engine.dispose()


@pytest.fixture(autouse=True)
def reads_logger(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    """Deliver the read logs to caplog alone; Alembic's fileConfig in the
    migration fixture disables existing loggers."""
    logger = logging.getLogger(READS_LOGGER)
    saved = (logger.disabled, logger.propagate, logger.level)
    logger.disabled, logger.propagate = False, False
    logger.setLevel(logging.INFO)
    logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        logger.removeHandler(caplog.handler)
        logger.disabled, logger.propagate = saved[:2]
        logger.setLevel(saved[2])


def read_logs(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        {name: record.__dict__[name] for name in TIMING_FIELDS}
        for record in caplog.records
        if record.name == READS_LOGGER
    ]


async def seed_day(api: AnalyticsApi) -> dict[str, Any]:
    """11-03 09:00~ (Seoul): editor, YouTube, a Korean site, opaque, a gap and a
    silence, with a completed session 09:00~10:00."""
    async with api.seeding() as seed:
        editor = seed.activity(540, 570, EDITOR)
        youtube = seed.activity(570, 580, YOUTUBE)
        korean = seed.activity(580, 581, KOREAN_SITE)
        seed.activity(581, 590, OPAQUE)
        seed.gap(590, 600)
        later = seed.activity(600, 610, NOTES)
        seed.confirm(editor, seed.coding)
        seed.confirm(youtube, seed.leisure)
        focus = seed.focus(540, 600, label_id=seed.coding)
    return {"editor": editor, "korean": korean, "later": later, "focus": focus}


@pytest.mark.asyncio
async def test_past_date_is_computed_from_the_source_with_no_store(
    api: AnalyticsApi, caplog: pytest.LogCaptureFixture
) -> None:
    await seed_day(api)
    caplog.clear()

    response = await api.daily(DAY)

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["date"] == "2026-11-03"
    assert body["observedThrough"] == iso(610)
    assert body["basis"] == {
        "status": "current",
        "definitionVersion": CURRENT_DEFINITION.version,
        "timezone": "Asia/Seoul",
    }
    usage = body["usage"]
    assert usage["apps"] == [
        {
            "bundleId": "com.example.editor",
            "displayName": "Editor",
            "durationUs": minutes_us(30),
        },
        {
            "bundleId": "com.google.Chrome",
            "displayName": "Chrome",
            "durationUs": minutes_us(11),
        },
        {
            "bundleId": "com.example.notes",
            "displayName": "Notes",
            "durationUs": minutes_us(10),
        },
    ]
    assert [row["target"] for row in usage["targets"]] == [
        {"kind": "app", "key": "com.example.editor", "displayName": "Editor"},
        {"kind": "app", "key": "com.example.notes", "displayName": "Notes"},
        {"kind": "site", "key": "youtube.com", "displayName": "youtube.com"},
        {
            "kind": "site",
            "key": "xn--vv4b11d.xn--3e0b707e",
            "displayName": "예시.한국",
        },
    ]
    assert usage["opaqueUs"] == minutes_us(9)
    assert usage["observedUs"] == minutes_us(60)
    assert usage["unknownAppUs"] == usage["browserWithoutUrlUs"] == 0
    assert body["targetTransitions"]["count"] == 2
    assert body["targetTransitions"]["items"][0] == {
        "at": iso(570),
        "previous": {
            "kind": "app",
            "key": "com.example.editor",
            "displayName": "Editor",
        },
        "next": {"kind": "site", "key": "youtube.com", "displayName": "youtube.com"},
        "previousDwellUs": minutes_us(30),
    }
    assert body["labelTransitions"]["count"] == 1
    assert body["labelTransitions"]["items"] == [
        {
            "at": iso(570),
            "previousLabelId": str(api.seed.coding),
            "nextLabelId": str(api.seed.leisure),
        }
    ]
    assert body["labelTransitions"]["pendingUs"] == minutes_us(11)
    assert body["collection"] == {"captureGapCount": 1, "silenceCount": 0}
    sessions = body["sessions"]
    assert sessions["count"] == 1
    assert sessions["composition"]["analysisUs"] == minutes_us(60)
    assert sessions["composition"]["focusUs"] == minutes_us(30)
    assert sessions["composition"]["lapseCount"] == 1
    assert [row["target"]["key"] for row in sessions["distractions"]] == ["youtube.com"]
    (log,) = read_logs(caplog)
    assert log["source"] == "computed"
    assert log["segment_count"] == 6
    assert min(log["source_read_ms"], log["snapshot_build_ms"]) >= 0


@pytest.mark.asyncio
async def test_future_date_is_empty_and_a_malformed_date_is_rejected(
    api: AnalyticsApi, caplog: pytest.LogCaptureFixture
) -> None:
    await seed_day(api)
    caplog.clear()

    future = await api.daily(date(2026, 11, 11))
    malformed = [await api.daily(value) for value in ("2026-13-01", "20261103", "")]

    assert future.status_code == 200
    assert future.headers["Cache-Control"] == "no-store"
    body = future.json()
    assert body["observedThrough"] == iso(610)
    assert body["usage"] == {
        "apps": [],
        "targets": [],
        "unknownAppUs": 0,
        "opaqueUs": 0,
        "observedUs": 0,
        "browserWithoutUrlUs": 0,
    }
    assert body["targetTransitions"] == {"count": 0, "shortDwellCount": 0, "items": []}
    assert body["labelTransitions"] == {"count": 0, "pendingUs": 0, "items": []}
    assert body["collection"] == {"captureGapCount": 0, "silenceCount": 0}
    assert body["sessions"]["count"] == 0
    assert set(body["sessions"]["composition"].values()) == {0}
    assert read_logs(caplog) == [
        {
            "source": "future",
            "source_read_ms": 0,
            "snapshot_build_ms": 0,
            "compute_ms": 0,
            "segment_count": 0,
        }
    ]
    for response in malformed:
        assert response.status_code == 422
        assert response.json()["error"]["status"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_stored_row_answer_equals_the_marked_recomputation(
    api: AnalyticsApi, caplog: pytest.LogCaptureFixture
) -> None:
    await seed_day(api)
    computed = (await api.daily(DAY)).json()
    await api.save_current_stats(DAY, source_version=1)
    caplog.clear()

    stored = await api.daily(DAY)
    await api.mark(DAY)
    marked = await api.daily(DAY)

    assert stored.status_code == marked.status_code == 200
    assert stored.json() == computed
    assert marked.json() == computed
    logs = read_logs(caplog)
    assert logs[0] == {
        "source": "stored",
        "source_read_ms": 0,
        "snapshot_build_ms": 0,
        "compute_ms": 0,
        "segment_count": 0,
    }
    assert logs[1]["source"] == "computed"
    assert logs[1]["segment_count"] == 6


@pytest.mark.asyncio
async def test_today_is_always_computed_even_with_a_stored_row(
    api: AnalyticsApi, caplog: pytest.LogCaptureFixture
) -> None:
    await seed_day(api)
    await api.save_stale_stats(DAY)
    api.clock.now = at(1000)
    caplog.clear()

    response = await api.daily(DAY)

    assert response.json()["usage"]["observedUs"] == minutes_us(60)
    assert read_logs(caplog)[0]["source"] == "computed"


@pytest.mark.asyncio
async def test_session_analysis_logs_the_read_timings(
    api: AnalyticsApi, caplog: pytest.LogCaptureFixture
) -> None:
    seeded = await seed_day(api)
    caplog.clear()

    response = await api.session_analysis(seeded["focus"].id)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    (log,) = read_logs(caplog)
    assert log["source"] == "computed"
    # The session window 09:00~10:00 reads the five segments before 10:00.
    assert log["segment_count"] == 5
    assert min(log["source_read_ms"], log["snapshot_build_ms"], log["compute_ms"]) >= 0


@pytest.mark.asyncio
async def test_accounts_see_only_their_own_values(api: AnalyticsApi) -> None:
    """API-3 (AC-8014)."""
    async with api.seeding() as seed:
        mine = seed.activity(540, 600, EDITOR)
        seed.confirm(mine, seed.coding)
        my_session = seed.focus(540, 600, label_id=seed.coding)
        in_progress = seed.focus(700, None)
        other = api.other
        theirs = other.activity(540, 600, detailed("com.other.app", name="Other"))
        other.confirm(theirs, other.leisure)
        their_session = other.focus(540, 600, label_id=other.leisure)

    daily = await api.daily(DAY)
    analysis = await api.session_analysis(my_session.id)
    foreign = await api.session_analysis(their_session.id)
    missing = await api.session_analysis(uuid4())
    running = await api.session_analysis(in_progress.id)
    their_daily = await api.daily(DAY, headers=api.other_headers)

    for response in (daily, analysis):
        assert response.status_code == 200
        text_body = response.text
        for value in ("com.other.app", str(other.leisure), str(their_session.id)):
            assert value not in text_body
    assert daily.json()["usage"]["observedUs"] == minutes_us(60)
    assert daily.json()["sessions"]["composition"]["focusUs"] == minutes_us(60)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["status"] == "ANALYTICS_FOCUS_SESSION_NOT_FOUND"
    assert foreign.json() == missing.json()
    assert running.status_code == 409
    assert running.json()["error"]["status"] == "ANALYTICS_FOCUS_SESSION_IN_PROGRESS"
    assert "com.example.editor" not in their_daily.text
    assert their_daily.json()["sessions"]["composition"]["focusUs"] == minutes_us(60)


@pytest.mark.asyncio
async def test_reads_reflect_corrections_and_late_observations(
    api: AnalyticsApi,
) -> None:
    """API-4 (AC-8013, AC-8021): the outdated confirmation leaves X pending."""
    async with api.seeding() as seed:
        x = seed.activity(540, 570, EDITOR)
        y = seed.activity(570, 600, NOTES)
        seed.confirm(x, seed.coding)
        y_confirmation = seed.confirm(y, seed.coding)
        focus = seed.focus(540, 600, label_id=seed.coding)
    before_day = (await api.daily(DAY)).json()
    before_session = (await api.session_analysis(focus.id)).json()

    async with api.seeding() as seed:
        await seed.session.execute(
            update(activity_label_confirmations)
            .where(activity_label_confirmations.c.id == y_confirmation.id)
            .values(label_id=seed.leisure)
        )
        late = seed.record(at(569))
        await seed.session.execute(
            update(activity_timeline_segments)
            .where(activity_timeline_segments.c.id == x.id)
            .values(last_event_id=late.event_id, last_observed_at=at(569))
        )
    after_day = (await api.daily(DAY)).json()
    after_session = (await api.session_analysis(focus.id)).json()

    assert before_session["composition"]["focusUs"] == minutes_us(60)
    assert before_day["labelTransitions"]["pendingUs"] == 0
    assert after_session["composition"] == {
        "analysisUs": minutes_us(60),
        "focusUs": 0,
        "lapseUs": minutes_us(30),
        "unclassifiedUs": 0,
        "pendingUs": minutes_us(30),
        "opaqueUs": 0,
        "unobservedUs": 0,
        "lapseCount": 1,
    }
    assert after_day["labelTransitions"]["pendingUs"] == minutes_us(30)
    assert after_day["sessions"]["composition"] == after_session["composition"]


@pytest.mark.asyncio
async def test_a_failing_date_fails_the_request_without_falling_back(
    api: AnalyticsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API-9 (AC-8024): the stale stored row of the failing date is not used."""
    await seed_day(api)
    await api.save_stale_stats(DAY)
    await api.mark(DAY)
    original = DailyStatsCalculator.compute

    def failing(self, snapshot, window):
        if window.day == DAY:
            raise RuntimeError("calculation failed")
        return original(self, snapshot, window)

    monkeypatch.setattr(DailyStatsCalculator, "compute", failing)

    failed = await api.daily(DAY)
    other_day = await api.daily(NEXT_DAY)

    assert failed.status_code == 500
    assert failed.json()["error"]["status"] == "INTERNAL_SERVER_ERROR"
    assert "calculation failed" not in failed.text
    assert other_day.status_code == 200


async def source_rows(api: AnalyticsApi) -> dict[str, list[tuple]]:
    async with api.factory() as session:
        rows = {}
        for name, table in {
            "segments": activity_timeline_segments,
            "confirmations": activity_label_confirmations,
            "sessions": focus_sessions,
            "markers": analytics_daily_markers,
            "stats": analytics_daily_stats,
        }.items():
            result = await session.execute(
                select(table)
                .where(table.c.account_id == api.seed.account_id)
                .order_by(*table.primary_key.columns)
            )
            rows[name] = [tuple(row) for row in result]
        timeline = create_observation_timeline()
        segments = await timeline.read_range(
            session,
            api.seed.account_id,
            start=at(-1440),
            end=at(2 * 1440),
            now=api.clock(),
        )
        rows["versions"] = [(segment.id, segment.version) for segment in segments]
    return rows


@pytest.mark.asyncio
async def test_reads_change_no_source_and_run_in_one_read_only_snapshot(
    api: AnalyticsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API-10."""
    seeded = await seed_day(api)
    await api.save_stale_stats(DAY)
    await api.mark(DAY)
    before = await source_rows(api)
    loader = api.analytics.source_loader
    original = loader.find_observed_through
    settings: list[tuple[str, str]] = []

    async def recording(db_session: AsyncSession, account_id: UUID):
        isolation = await db_session.scalar(text("SHOW transaction_isolation"))
        read_only = await db_session.scalar(text("SHOW transaction_read_only"))
        settings.append((isolation, read_only))
        return await original(db_session, account_id)

    monkeypatch.setattr(loader, "find_observed_through", recording)

    responses = [
        await api.daily(DAY),
        await api.daily(NEXT_DAY),
        await api.session_analysis(seeded["focus"].id),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert settings == [("repeatable read", "on")] * 3
    assert await source_rows(api) == before
    assert before["markers"]
    assert before["stats"]


@pytest.mark.asyncio
async def test_opaque_app_and_context_values_are_never_returned(
    api: AnalyticsApi,
) -> None:
    """API-11 (AC-8009, E8)."""
    hidden = {
        "kind": "opaque",
        "app": {"bundleId": {"status": "captured", "value": "com.secret.app"}},
        "window": {"title": {"status": "captured", "value": "secret-title"}},
    }
    async with api.seeding() as seed:
        focus_start = seed.activity(540, 550, EDITOR)
        opaque = seed.activity(550, 560, hidden)
        lapse = seed.activity(560, 570, YOUTUBE)
        seed.confirm(focus_start, seed.coding)
        seed.confirm(opaque, seed.leisure)
        seed.confirm(lapse, seed.leisure)
        focus = seed.focus(540, 600, label_id=seed.coding)

    daily = await api.daily(DAY)
    analysis = await api.session_analysis(focus.id)

    for response in (daily, analysis):
        assert response.status_code == 200
        assert "secret" not in response.text
    body = analysis.json()
    assert body["composition"]["opaqueUs"] == minutes_us(10)
    (evidence,) = body["lapses"]
    assert evidence["entry"]["target"]["key"] == "youtube.com"
    context = evidence["entry"]["context"]
    assert context["app"] == YOUTUBE["app"]
    assert context["web"]["url"] == YOUTUBE["web"]["url"]
    assert evidence["entry"]["labelId"] == str(api.seed.leisure)
    assert evidence["previousFocusTarget"] == {
        "kind": "app",
        "key": "com.example.editor",
        "displayName": "Editor",
    }


@pytest.mark.asyncio
async def test_observed_through_is_the_last_observation_not_the_read_time(
    api: AnalyticsApi,
) -> None:
    """API-13 (AC-8030): last observation 14:32, read at 15:10."""
    async with api.seeding() as seed:
        seed.activity(800, None, EDITOR, last=872)
        focus = seed.focus(800, 870, label_id=seed.coding)
    api.clock.now = at(910)

    daily = await api.daily(DAY)
    analysis = await api.session_analysis(focus.id)

    assert daily.json()["observedThrough"] == "2026-11-03T05:32:00Z"
    assert analysis.json()["observedThrough"] == "2026-11-03T05:32:00Z"
    assert daily.json()["usage"]["observedUs"] == minutes_us(72)


def after_first_call(
    target: object,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
    action: Callable[[], Awaitable[None]],
) -> None:
    """Run `action` (on other connections) once, right after the first call."""
    original = getattr(target, name)
    done: list[bool] = []

    async def wrapper(self, *args: Any, **kwargs: Any) -> Any:
        result = await original(self, *args, **kwargs)
        if not done:
            done.append(True)
            await action()
        return result

    monkeypatch.setattr(target, name, wrapper)


@pytest.mark.asyncio
async def test_stale_marked_date_is_recomputed_even_if_the_batch_commits_meanwhile(
    api: AnalyticsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TX-10: the read saw the marker, so it computes the current value."""
    await seed_day(api)
    expected = (await api.daily(DAY)).json()
    await api.save_stale_stats(DAY)
    await api.mark(DAY)

    async def batch_commit() -> None:
        await api.save_current_stats(DAY, source_version=10**9)
        async with api.factory() as session:
            await session.execute(
                analytics_daily_markers.delete().where(
                    analytics_daily_markers.c.account_id == api.seed.account_id
                )
            )
            await session.commit()

    after_first_call(RecomputeMarkerRepository, "find", monkeypatch, batch_commit)

    during = await api.daily(DAY)
    after = await api.daily(DAY)

    assert during.json() == expected
    assert after.json() == expected
    assert expected["usage"]["observedUs"] != daily_stats().usage.observed_us


@pytest.mark.asyncio
async def test_source_commits_after_the_first_read_do_not_leak_into_the_result(
    api: AnalyticsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TX-11: the result is the source as of the read's snapshot."""
    seeded = await seed_day(api)
    expected = (await api.daily(DAY)).json()

    async def two_source_commits() -> None:
        async with api.seeding() as seed:
            seed.confirm(seeded["later"], seed.leisure)
        async with api.seeding() as seed:
            seed.activity(700, 760, EDITOR)

    after_first_call(ObservationTimeline, "read_range", monkeypatch, two_source_commits)

    during = await api.daily(DAY)
    after = await api.daily(DAY)

    assert during.json() == expected
    assert after.json() != expected
    assert after.json()["usage"]["observedUs"] == minutes_us(120)


@pytest.mark.asyncio
async def test_pauses_are_left_out_and_missing_pause_info_is_reported(
    api: AnalyticsApi,
) -> None:
    """AC-8010, AC-8011."""
    async with api.seeding() as seed:
        seed.confirm(seed.activity(540, 550, EDITOR), seed.coding)
        seed.confirm(seed.activity(550, 560, YOUTUBE), seed.leisure)
        seed.confirm(seed.activity(560, 570, EDITOR), seed.coding)
        seed.confirm(seed.activity(570, 575, YOUTUBE), seed.leisure)
        seed.confirm(seed.activity(575, 600, EDITOR), seed.coding)
        paused = seed.focus(540, 600, pauses=[(550, 560)])
        no_info = seed.focus(1540, 1600)
    async with api.seeding() as seed:
        seed.confirm(seed.activity(1540, 1600, YOUTUBE), seed.leisure)

    with_pause = (await api.session_analysis(paused.id)).json()
    without = (await api.session_analysis(no_info.id)).json()

    assert with_pause["hasPauseInfo"] is True
    assert with_pause["pauses"] == [{"startedAt": iso(550), "endedAt": iso(560)}]
    assert with_pause["composition"]["analysisUs"] == minutes_us(50)
    assert with_pause["composition"]["lapseUs"] == minutes_us(5)
    assert with_pause["composition"]["lapseCount"] == 1
    assert [lapse["startedAt"] for lapse in with_pause["lapses"]] == [iso(570)]
    assert without["hasPauseInfo"] is False
    assert without["pauses"] == []
    assert without["startedAt"] == iso(1540)
    assert without["endedAt"] == iso(1600)
    assert without["composition"]["analysisUs"] == minutes_us(60)
    assert without["composition"]["lapseUs"] == minutes_us(60)


@pytest.mark.asyncio
async def test_api_assembly_routes_and_authenticates_the_analytics_reads(
    api: AnalyticsApi, identity_config: IdentityConfig
) -> None:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_current_account] = current_account_provider(
        identity_config
    )
    app.dependency_overrides[get_session] = api.session_override
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        anonymous = [
            await client.get("/api/v1/analytics/daily-stats?date=2026-11-03"),
            await client.get(f"/api/v1/analytics/focus-sessions/{uuid4()}"),
        ]
        future = await client.get(
            "/api/v1/analytics/daily-stats?date=2999-01-01", headers=api.headers
        )

    for response in anonymous:
        assert response.status_code == 401
        assert response.json()["error"]["status"] == "AUTH_INVALID_ACCESS_TOKEN"
    assert future.status_code == 200
    assert future.headers["Cache-Control"] == "no-store"
    assert future.json()["usage"]["observedUs"] == 0

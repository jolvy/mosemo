"""Analytics repositories against PostgreSQL: markers, daily stats and definition."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.domain.definition import CURRENT_DEFINITION
from mosemo.analytics.domain.markers import (
    RETRY_POLICY,
    MarkerKind,
    MarkScope,
    RecomputeMarker,
)
from mosemo.analytics.domain.stored_stats import StoredDailyStats
from mosemo.analytics.infrastructure.daily_stats import DailyStatsRepository
from mosemo.analytics.infrastructure.definitions import AnalyticsDefinitionQuery
from mosemo.analytics.infrastructure.markers import (
    RecomputeMarkerRepository,
    due_markers,
)
from mosemo.analytics.infrastructure.tables import (
    analytics_daily_markers,
    marker_version_seq,
)
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.shared.domain.timezones import Timezone
from tests.support.daily_stats import daily_stats

# Far before any marker other tests leave behind, so only these rows are due.
NOW = datetime(2000, 6, 1, tzinfo=UTC)
DAY = date(2000, 5, 1)
SEOUL = Timezone.ASIA_SEOUL


async def new_account(session: AsyncSession) -> UUID:
    account = Account(provider=AccountProvider.KAKAO, provider_subject=str(uuid4()))
    session.add(account)
    await session.flush()
    return account.id


async def set_marker(
    session: AsyncSession, account_id: UUID, local_date: date, **values: object
) -> None:
    await session.execute(
        update(analytics_daily_markers)
        .where(
            analytics_daily_markers.c.account_id == account_id,
            analytics_daily_markers.c.local_date == local_date,
        )
        .values(**values)
    )


@pytest.mark.asyncio
async def test_upsert_source_writes_a_new_version_and_resets_the_retry_state(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    markers = RecomputeMarkerRepository()
    account_id = await new_account(session)
    eligible = MarkScope.eligible_at(DAY, SEOUL)

    await markers.upsert_source(
        session, account_id=account_id, local_date=DAY, eligible_at=eligible, now=NOW
    )
    first = await markers.find(session, account_id=account_id, local_date=DAY)
    assert first is not None
    assert (first.kind, first.attempts, first.eligible_at) == (
        MarkerKind.SOURCE,
        0,
        eligible,
    )
    assert (first.marked_at, first.next_attempt_at) == (NOW, NOW)
    first_version = first.version

    await set_marker(
        session,
        account_id,
        DAY,
        kind="definition",
        attempts=4,
        next_attempt_at=NOW + timedelta(hours=2),
        last_error="TimeoutError",
        last_failed_at=NOW,
    )
    later = NOW + timedelta(minutes=1)
    await markers.upsert_source(
        session, account_id=account_id, local_date=DAY, eligible_at=eligible, now=later
    )
    second = await markers.find(session, account_id=account_id, local_date=DAY)
    assert second is not None
    assert second.version > first_version
    assert (second.kind, second.attempts, second.next_attempt_at) == (
        MarkerKind.SOURCE,
        0,
        later,
    )
    assert (second.last_error, second.last_failed_at) == (None, None)


@pytest.mark.asyncio
async def test_delete_and_failure_apply_only_to_the_claimed_version(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    markers = RecomputeMarkerRepository()
    account_id = await new_account(session)
    eligible = MarkScope.eligible_at(DAY, SEOUL)
    await markers.upsert_source(
        session, account_id=account_id, local_date=DAY, eligible_at=eligible, now=NOW
    )
    (claimed,) = await markers.claim_due(
        session,
        now=NOW + timedelta(days=1),
        limit=1,
        definition_limit=0,
        policy=RETRY_POLICY,
    )
    session.expunge(claimed)
    failed = replace_marker(claimed)
    failed.fail("ValueError", now=NOW, policy=RETRY_POLICY)

    await markers.upsert_source(
        session, account_id=account_id, local_date=DAY, eligible_at=eligible, now=NOW
    )
    assert not await markers.delete_if_version(session, claimed.version)
    assert not await markers.record_failure(session, failed)
    current = await markers.find(session, account_id=account_id, local_date=DAY)
    assert current is not None
    assert current.last_error is None

    newer = replace_marker(current)
    newer.fail("ValueError", now=NOW, policy=RETRY_POLICY)
    assert await markers.record_failure(session, newer)
    stored = await markers.find(session, account_id=account_id, local_date=DAY)
    assert stored is not None
    assert (stored.last_error, stored.last_failed_at) == ("ValueError", NOW)
    assert await markers.delete_if_version(session, current.version)
    assert await markers.find(session, account_id=account_id, local_date=DAY) is None


def replace_marker(marker: RecomputeMarker) -> RecomputeMarker:
    """A detached copy, as the batch holds it after its claim transaction."""
    return RecomputeMarker(
        account_id=marker.account_id,
        local_date=marker.local_date,
        version=marker.version,
        kind=marker.kind,
        marked_at=marker.marked_at,
        eligible_at=marker.eligible_at,
        attempts=marker.attempts,
        next_attempt_at=marker.next_attempt_at,
    )


@pytest.mark.asyncio
async def test_claim_due_skips_waiting_and_quarantined_and_takes_source_first(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    markers = RecomputeMarkerRepository()
    account_id = await new_account(session)
    days = [DAY + timedelta(days=n) for n in range(6)]
    for day in days:
        await markers.upsert_source(
            session,
            account_id=account_id,
            local_date=day,
            eligible_at=NOW - timedelta(days=1),
            now=NOW - timedelta(days=1),
        )
    definition, source, eligible_later, waiting, quarantined, second_definition = days
    await set_marker(session, account_id, definition, kind="definition")
    await set_marker(session, account_id, second_definition, kind="definition")
    await set_marker(
        session, account_id, eligible_later, eligible_at=NOW + timedelta(seconds=1)
    )
    await set_marker(
        session, account_id, waiting, next_attempt_at=NOW + timedelta(seconds=1)
    )
    await set_marker(session, account_id, quarantined, attempts=6)

    claimed = await markers.claim_due(
        session, now=NOW, limit=2, definition_limit=5, policy=RETRY_POLICY
    )

    # The definition marker has the smaller version but source comes first.
    assert [marker.local_date for marker in claimed] == [source, definition]
    for day in (source, definition):
        stored = await markers.find(session, account_id=account_id, local_date=day)
        assert stored is not None
        assert stored.attempts == 1
        assert stored.next_attempt_at == NOW + RETRY_POLICY.lease
    # Leased now, so a second claim at the same time finds the rest only.
    again = await markers.claim_due(
        session, now=NOW, limit=10, definition_limit=0, policy=RETRY_POLICY
    )
    assert again == []
    capped = await markers.claim_due(
        session, now=NOW, limit=10, definition_limit=1, policy=RETRY_POLICY
    )
    assert [marker.local_date for marker in capped] == [second_definition]


@pytest.mark.asyncio
async def test_claim_query_uses_the_partial_claim_index(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    account_id = await new_account(session)

    def row(n: int, *, due: bool, attempts: int) -> dict[str, object]:
        at = NOW - timedelta(days=1) if due else NOW + timedelta(days=1)
        return {
            "account_id": account_id,
            "local_date": DAY + timedelta(days=n),
            "version": marker_version_seq.next_value(),
            "kind": MarkerKind.SOURCE,
            "marked_at": NOW,
            "eligible_at": at,
            "attempts": attempts,
            "next_attempt_at": at,
        }

    rows = [row(n, due=n < 100, attempts=0) for n in range(2000)]
    rows += [row(n, due=True, attempts=6) for n in range(2000, 2500)]
    for row_values in rows:
        await session.execute(insert(analytics_daily_markers).values(row_values))
    await session.execute(text("ANALYZE analytics_daily_markers"))

    query = due_markers(definition=False, now=NOW, limit=50, policy=RETRY_POLICY)
    sql = query.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    plan = "\n".join((await session.execute(text(f"EXPLAIN {sql}"))).scalars())

    assert "analytics_daily_markers_claim_idx" in plan, plan


@pytest.mark.asyncio
async def test_save_if_newer_keeps_the_larger_source_version(
    integration_session: AsyncSession,
) -> None:
    session = integration_session
    repository = DailyStatsRepository()
    account_id = await new_account(session)
    stored = StoredDailyStats(
        account_id=account_id,
        local_date=DAY,
        source_version=5,
        definition_version=CURRENT_DEFINITION.version,
        timezone=SEOUL,
        computed_at=NOW,
        stats=daily_stats(),
    )

    assert await repository.save_if_newer(session, stored)
    older = replace(stored, source_version=4, timezone=Timezone.UTC)
    assert not await repository.save_if_newer(session, older)
    loaded = await repository.find(session, account_id=account_id, local_date=DAY)
    assert loaded is not None
    assert (loaded.source_version, loaded.timezone) == (5, SEOUL)
    assert loaded.stats == stored.stats
    assert loaded.is_current_for(CURRENT_DEFINITION.version, SEOUL)

    newer = replace(stored, source_version=6, timezone=Timezone.UTC)
    assert await repository.save_if_newer(session, newer)
    loaded = await repository.find(session, account_id=account_id, local_date=DAY)
    assert loaded is not None
    assert (loaded.source_version, loaded.timezone) == (6, Timezone.UTC)


@pytest.mark.asyncio
async def test_definition_query_reads_the_current_definition_version(
    integration_session: AsyncSession,
) -> None:
    query = AnalyticsDefinitionQuery()

    assert await query.read(integration_session) == CURRENT_DEFINITION.version
    assert await query.read_for_share(integration_session) == CURRENT_DEFINITION.version

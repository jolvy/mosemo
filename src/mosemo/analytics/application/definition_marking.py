"""Definition reprocessing marks (ADR 0016 「실패·재처리」 「정의 변경 절차」).

Run only after the definition row was raised: the marks get versions larger
than every stored `source_version`, so the batch replaces the old rows through
the usual guard.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.domain.definition import AnalyticsDefinition
from mosemo.analytics.domain.markers import MarkScope
from mosemo.analytics.infrastructure.daily_stats import OutdatedStatsQuery
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.identity.public import AccountDirectory
from mosemo.observation.public import ObservationTimeline
from mosemo.platform.database.transactions import (
    read_snapshot,
    reset_session,
    unit_of_work,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.application.use_case import UseCase


@dataclass(kw_only=True)
class MarkReport:
    accounts: int = 0
    markers: int = 0

    def fields(self) -> dict[str, object]:
        return {"accounts": self.accounts, "markers": self.markers}


class DefinitionMarkWriter:
    """Marks one account's outdated dates inside the caller's transaction."""

    def __init__(
        self,
        *,
        timeline: ObservationTimeline,
        accounts: AccountDirectory,
        outdated: OutdatedStatsQuery,
        markers: RecomputeMarkerRepository,
        definition: AnalyticsDefinition,
    ) -> None:
        self._timeline = timeline
        self._accounts = accounts
        self._outdated = outdated
        self._markers = markers
        self._definition = definition

    async def list_accounts(self, db_session: AsyncSession) -> list[UUID]:
        return await self._outdated.list_accounts(
            db_session, below=self._definition.version
        )

    async def mark_account(
        self, db_session: AsyncSession, account_id: UUID, *, now: datetime
    ) -> int:
        """Take the account lock (3s `lock_timeout`) before reading the timezone
        and the dates, so no source change draws a version outside it."""
        await self._timeline.lock(db_session, account_id)
        timezone = await self._accounts.get_timezone(db_session, account_id)
        dates = await self._outdated.list_dates(
            db_session, account_id, below=self._definition.version
        )
        for local_date in dates:
            await self._markers.upsert_definition(
                db_session,
                account_id=account_id,
                local_date=local_date,
                eligible_at=MarkScope.eligible_at(local_date, timezone),
                now=now,
            )
        return len(dates)


class MarkOutdatedDefinition(UseCase):
    """Job `analytics-definition-mark`: one transaction per account, in account
    id order. Safe to rerun: dates already stored with the current definition
    are left out."""

    def __init__(
        self,
        *,
        writer: DefinitionMarkWriter,
        dispatcher: EventDispatcher,
        clock: Clock,
    ) -> None:
        self._writer = writer
        self._dispatcher = dispatcher
        self._clock = clock

    async def execute(self, db_session: AsyncSession) -> MarkReport:
        report = MarkReport()
        async with read_snapshot(db_session):
            account_ids = await self._writer.list_accounts(db_session)
        for account_id in account_ids:
            reset_session(db_session)
            async with unit_of_work(db_session, self._dispatcher):
                report.markers += await self._writer.mark_account(
                    db_session, account_id, now=self._clock()
                )
            report.accounts += 1
        return report

"""Labeling test helpers: the old proposal processor and scanner calls over the
proposal use cases, each call in a fresh session as the removed classes did."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.labeling.application.ports import LabelSuggester
from mosemo.labeling.application.proposals import (
    MAX_SCAN_CANDIDATES_PER_BATCH,
    ProposalClaim,
    ProposalScanBatch,
    ProposeLabel,
    ScanLabelProposals,
    SweepLabelProposals,
)
from mosemo.labeling.bootstrap import LabelProposals
from mosemo.labeling.infrastructure.confirmations import ConfirmationRepository
from mosemo.labeling.infrastructure.proposals import ProposalRepository
from mosemo.labels.public import LabelCatalog
from mosemo.observation.public import ObservationTimeline
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher


class ProposalProcessor:
    """Runs `ProposeLabel` for one candidate in a fresh session."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        suggester: LabelSuggester,
        clock: Clock,
        labels: LabelCatalog,
        timeline: ObservationTimeline,
    ) -> None:
        self._session_factory = session_factory
        self._propose = LabelProposals(
            suggester=suggester,
            labels=labels,
            timeline=timeline,
            dispatcher=EventDispatcher(),
            clock=clock,
        ).propose_label

    async def process_candidate(self, account_id: UUID, first_event_id: UUID) -> bool:
        async with self._session_factory() as db_session:
            return await self._propose.execute(
                db_session, account_id=account_id, first_event_id=first_event_id
            )

    async def _claim(self, account_id: UUID, first_event_id: UUID) -> ProposalClaim:
        async with self._session_factory() as db_session:
            return await self._propose._claim(
                db_session, account_id=account_id, first_event_id=first_event_id
            )


class _ProcessorProposeLabel(ProposeLabel):
    """Hands each scanned candidate to the test processor, so a processor
    subclass sees the candidates the scanner picks."""

    def __init__(self, processor: ProposalProcessor) -> None:
        self._processor = processor

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, first_event_id: UUID
    ) -> bool:
        return await self._processor.process_candidate(account_id, first_event_id)


class ProposalScanner:
    """Runs the scan use cases with a cursor kept between `run_batch` calls."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        processor: ProposalProcessor,
        *,
        clock: Clock,
        timeline: ObservationTimeline,
    ) -> None:
        self._session_factory = session_factory
        self._scan = ScanLabelProposals(
            propose=_ProcessorProposeLabel(processor),
            confirmations=ConfirmationRepository(),
            proposals=ProposalRepository(),
            timeline=timeline,
            clock=clock,
        )
        self._last_segment_id: UUID | None = None

    async def run_once(self, *, max_processed: int = 100) -> int:
        processed = 0
        while processed < max_processed:
            batch = await self.run_batch(
                max_processed=max_processed - processed,
                max_scanned=MAX_SCAN_CANDIDATES_PER_BATCH,
            )
            processed += batch.processed
            if batch.sweep_complete:
                break
        return processed

    async def run_sweep(
        self, *, max_scanned: int = MAX_SCAN_CANDIDATES_PER_BATCH
    ) -> int:
        async with self._session_factory() as db_session:
            return await SweepLabelProposals(scan=self._scan).execute(
                db_session, max_scanned=max_scanned
            )

    async def run_batch(
        self,
        *,
        max_processed: int = 100,
        max_scanned: int = MAX_SCAN_CANDIDATES_PER_BATCH,
    ) -> ProposalScanBatch:
        async with self._session_factory() as db_session:
            batch = await self._scan.execute(
                db_session,
                after_segment_id=self._last_segment_id,
                max_processed=max_processed,
                max_scanned=max_scanned,
            )
        self._last_segment_id = batch.last_segment_id
        return batch

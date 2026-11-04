import logging
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activity_labels.confirmations.models import ActivityLabelConfirmation
from mosemo.activity_labels.confirmations.repository import ConfirmationRepository
from mosemo.activity_labels.proposals.models import (
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
    ProposalClaimResult,
    ProposalCompletion,
)
from mosemo.activity_labels.proposals.repository import ProposalRepository
from mosemo.activity_labels.proposals.suggestions import (
    ConfirmedExample,
    LabelCandidate,
    LabelSuggester,
    SuggestionInput,
    SuggestionResult,
    summarize_context,
)
from mosemo.labels.public import LabelCatalog
from mosemo.observation.public import (
    ActivityTimelineBusyError,
    ActivityTimelineKind,
    ObservationTimeline,
    ObservedSegment,
)
from mosemo.shared.application.clock import Clock

MAX_EXAMPLES = 8
SCAN_PAGE_SIZE = 100
MAX_SCAN_CANDIDATES_PER_BATCH = 500
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Claim:
    account_id: UUID
    first_event_id: UUID
    proposal_id: UUID
    lease_token: UUID
    segment_version: str
    activity: str


@dataclass(frozen=True)
class _ScannedSegment:
    segment_id: UUID
    account_id: UUID
    first_event_id: UUID
    needs_processing: bool


@dataclass(frozen=True)
class ProposalScanBatch:
    processed: int
    scanned: int
    sweep_complete: bool


class NoProposalWorkError(Exception):
    """No current proposal attempt can be claimed for this candidate."""


def _current_version(segment: ObservedSegment | None) -> str | None:
    if (
        segment is None
        or segment.kind is not ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY
    ):
        return None
    return segment.version


def _needs_processing(
    confirmation: ActivityLabelConfirmation | None,
    proposal: ActivityLabelProposal | None,
    *,
    version: str,
    now: datetime,
) -> bool:
    if confirmation is not None and confirmation.applies_to(version):
        return False
    return proposal is None or proposal.needs_worker_attention(now)


class RecentConfirmedExampleRetriever:
    """Find recent confirmations that still apply to the current suggestion input."""

    PAGE_SIZE = 40

    def __init__(self, session: AsyncSession, *, timeline: ObservationTimeline) -> None:
        self._session = session
        self._confirmation_repository = ConfirmationRepository(session)
        self._timeline = timeline

    async def find(
        self,
        *,
        account_id: UUID,
        exclude_first_event_id: UUID,
        active_label_ids: set[UUID],
        now: datetime,
    ) -> list[ConfirmedExample]:
        examples: list[ConfirmedExample] = []
        offset = 0
        while len(examples) < MAX_EXAMPLES:
            rows = await self._confirmation_repository.list_example_page(
                account_id=account_id,
                exclude_first_event_id=exclude_first_event_id,
                limit=self.PAGE_SIZE,
                offset=offset,
            )
            if not rows:
                break
            segments = await self._timeline.find_by_first_events(
                self._session,
                account_id=account_id,
                first_event_ids={confirmation.first_event_id for confirmation in rows},
                now=now,
            )
            for confirmation in rows:
                segment = segments.get(confirmation.first_event_id)
                if segment is None or not self._is_usable_as_suggestion_example(
                    confirmation, segment, active_label_ids=active_label_ids
                ):
                    continue
                assert segment.context is not None
                examples.append(
                    ConfirmedExample(
                        confirmation_id=confirmation.confirmation_id,
                        activity=summarize_context(segment.context),
                        label_id=confirmation.label_id,
                    )
                )
                if len(examples) == MAX_EXAMPLES:
                    break
            if len(rows) < self.PAGE_SIZE:
                break
            offset += len(rows)
        return examples

    @staticmethod
    def _is_usable_as_suggestion_example(
        confirmation: ActivityLabelConfirmation,
        segment: ObservedSegment,
        *,
        active_label_ids: set[UUID],
    ) -> bool:
        version = _current_version(segment)
        return (
            version is not None
            and confirmation.applies_to(version)
            and (
                confirmation.label_id is None
                or confirmation.label_id in active_label_ids
            )
        )


class ProposalProcessor:
    """Process a candidate independently of whether a DB poller or queue found it."""

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
        self._suggester = suggester
        self._clock = clock
        self._labels = labels
        self._timeline = timeline

    async def process_candidate(self, account_id: UUID, first_event_id: UUID) -> bool:
        try:
            claim = await self._claim(account_id, first_event_id)
        except NoProposalWorkError:
            return False

        request: SuggestionInput | None = None
        model_started: float | None = None
        try:
            request = await self._build_suggestion_input(
                account_id=account_id,
                first_event_id=first_event_id,
                activity=claim.activity,
            )
            model_started = perf_counter()
            result = await self._suggester.suggest(request)
        except Exception:
            logger.exception("label proposal generation failed")
            result = None
        latency_ms = (
            round((perf_counter() - model_started) * 1000)
            if model_started is not None
            else 0
        )
        await self._finish(
            claim=claim,
            request=request,
            result=result,
            latency_ms=latency_ms,
        )
        return True

    async def _build_suggestion_input(
        self,
        *,
        account_id: UUID,
        first_event_id: UUID,
        activity: str,
    ) -> SuggestionInput:
        now = self._clock()
        async with self._session_factory() as session:
            labels = await self._labels.list_active(session, account_id)
            examples = await RecentConfirmedExampleRetriever(
                session, timeline=self._timeline
            ).find(
                account_id=account_id,
                exclude_first_event_id=first_event_id,
                active_label_ids={label.id for label in labels},
                now=now,
            )
        return SuggestionInput(
            activity=activity,
            labels=tuple(
                LabelCandidate(label.id, label.display_name) for label in labels
            ),
            examples=tuple(examples),
        )

    async def _claim(self, account_id: UUID, first_event_id: UUID) -> _Claim:
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            await self._timeline.lock(session, account_id)
            confirmation_repository = ConfirmationRepository(session)
            segment = await self._timeline.find_by_first_event(
                session, account_id=account_id, first_event_id=first_event_id, now=now
            )
            version = _current_version(segment)
            if segment is None or version is None:
                raise NoProposalWorkError
            confirmation = await confirmation_repository.find_confirmation(
                account_id=account_id, first_event_id=first_event_id
            )
            if confirmation is not None and confirmation.applies_to(version):
                raise NoProposalWorkError
            proposal_repository = ProposalRepository(session)
            proposal = await proposal_repository.find_proposal(
                account_id=account_id,
                first_event_id=first_event_id,
                segment_version=version,
            )
            if proposal is None:
                proposal = ActivityLabelProposal(
                    account_id=account_id,
                    first_event_id=first_event_id,
                    segment_version=version,
                    status=ActivityLabelProposalStatus.PROCESSING,
                    attempt_count=0,
                )
                proposal_repository.add(proposal)

            token = uuid4()
            claim_result = proposal.try_start_attempt(now=now, token=token)
            if claim_result is ProposalClaimResult.CLAIMED:
                assert segment.context is not None
                await session.flush()
                return _Claim(
                    account_id=account_id,
                    first_event_id=first_event_id,
                    proposal_id=proposal.proposal_id,
                    lease_token=token,
                    segment_version=version,
                    activity=summarize_context(segment.context),
                )

        # Commit any expired final-lease cleanup before signaling no work.
        raise NoProposalWorkError

    async def _finish(
        self,
        *,
        claim: _Claim,
        request: SuggestionInput | None,
        result: SuggestionResult | None,
        latency_ms: int,
    ) -> None:
        account_id = claim.account_id
        first_event_id = claim.first_event_id
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            await self._timeline.lock(session, account_id)
            proposal = await ProposalRepository(session).find_by_id(claim.proposal_id)
            if proposal is None or not proposal.owns_attempt(claim.lease_token):
                return
            confirmation_repository = ConfirmationRepository(session)
            segment = await self._timeline.find_by_first_event(
                session, account_id=account_id, first_event_id=first_event_id, now=now
            )
            confirmation = await confirmation_repository.find_confirmation(
                account_id=account_id, first_event_id=first_event_id
            )
            if _current_version(segment) != claim.segment_version or (
                confirmation is not None
                and confirmation.applies_to(claim.segment_version)
            ):
                proposal.supersede_attempt(token=claim.lease_token)
            elif result is None or (
                request is None
                or (
                    result.label_id is not None
                    and (
                        result.label_id
                        not in {label.label_id for label in request.labels}
                        or not await self._labels.is_active(
                            session, account_id=account_id, label_id=result.label_id
                        )
                    )
                )
            ):
                proposal.fail_attempt(token=claim.lease_token, now=now)
            else:
                assert request is not None
                completion = ProposalCompletion(
                    label_id=result.label_id,
                    provider=result.provider,
                    model=result.model,
                    prompt_version=result.prompt_version,
                    retrieved_example_ids=tuple(
                        str(example.confirmation_id) for example in request.examples
                    ),
                    latency_ms=latency_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
                proposal.complete_attempt(
                    token=claim.lease_token,
                    completion=completion,
                    now=now,
                )


class ProposalScanner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        processor: ProposalProcessor,
        *,
        clock: Clock,
        timeline: ObservationTimeline,
    ) -> None:
        self._session_factory = session_factory
        self._processor = processor
        self._clock = clock
        self._timeline = timeline
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
        # The segment cursor only moves forward, so the sweep completes unless new
        # closed segments keep arriving faster than they are processed.
        processed = 0
        while True:
            batch = await self.run_batch(max_scanned=max_scanned)
            processed += batch.processed
            if batch.sweep_complete:
                return processed

    async def run_batch(
        self,
        *,
        max_processed: int = 100,
        max_scanned: int = MAX_SCAN_CANDIDATES_PER_BATCH,
    ) -> ProposalScanBatch:
        processed = 0
        scanned = 0
        while processed < max_processed and scanned < max_scanned:
            page = await self._find_page(
                limit=min(SCAN_PAGE_SIZE, max_scanned - scanned)
            )
            if not page:
                self._last_segment_id = None
                return ProposalScanBatch(processed, scanned, sweep_complete=True)

            page_processed, page_scanned = await self._process_page(
                page, max_processed=max_processed - processed
            )
            processed += page_processed
            scanned += page_scanned
        return ProposalScanBatch(processed, scanned, sweep_complete=False)

    async def _process_page(
        self, page: list[_ScannedSegment], *, max_processed: int
    ) -> tuple[int, int]:
        processed = 0
        scanned = 0
        for segment in page:
            scanned += 1
            self._last_segment_id = segment.segment_id
            if not segment.needs_processing:
                continue
            try:
                did_process = await self._processor.process_candidate(
                    segment.account_id, segment.first_event_id
                )
            except ActivityTimelineBusyError:
                continue
            if did_process:
                processed += 1
                if processed == max_processed:
                    break
        return processed, scanned

    async def _find_page(self, *, limit: int) -> list[_ScannedSegment]:
        now = self._clock()
        async with self._session_factory() as session:
            segments = await self._timeline.list_closed_page(
                session, after_segment_id=self._last_segment_id, now=now, limit=limit
            )
            versioned_segments = [
                (segment, _current_version(segment)) for segment in segments
            ]
            current_segments = [
                (segment, version)
                for segment, version in versioned_segments
                if version is not None
            ]
            if not current_segments:
                return [
                    _ScannedSegment(
                        segment.id,
                        segment.account_id,
                        segment.first_event_id,
                        needs_processing=False,
                    )
                    for segment in segments
                ]

            confirmation_keys = [
                (segment.account_id, segment.first_event_id)
                for segment, _ in current_segments
            ]
            confirmations = await ConfirmationRepository(session).list_by_keys(
                confirmation_keys
            )
            current_confirmations = {
                (
                    confirmation.account_id,
                    confirmation.first_event_id,
                ): confirmation
                for confirmation in confirmations
            }

            proposal_keys = [
                (segment.account_id, segment.first_event_id, version)
                for segment, version in current_segments
            ]
            proposals = await ProposalRepository(session).list_by_keys(proposal_keys)
            current_proposals = {
                (
                    proposal.account_id,
                    proposal.first_event_id,
                    proposal.segment_version,
                ): proposal
                for proposal in proposals
            }

            page: list[_ScannedSegment] = []
            for segment, version in versioned_segments:
                needs_processing = False
                if version is not None:
                    confirmation = current_confirmations.get(
                        (segment.account_id, segment.first_event_id)
                    )
                    proposal = current_proposals.get(
                        (segment.account_id, segment.first_event_id, version)
                    )
                    needs_processing = _needs_processing(
                        confirmation, proposal, version=version, now=now
                    )
                page.append(
                    _ScannedSegment(
                        segment.id,
                        segment.account_id,
                        segment.first_event_id,
                        needs_processing,
                    )
                )
            return page

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mosemo.activities.constants import MAX_OBSERVATION_GAP
from mosemo.activities.enums import ActivityTimelineKind, SegmentType
from mosemo.activities.models import ActivityTimelineSegment
from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import (
    ActivityTimelineBusyError,
    acquire_activity_timeline_lock,
)
from mosemo.labels.models import (
    ActivityLabelConfirmation,
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
    ProposalClaimResult,
    ProposalCompletion,
)
from mosemo.labels.repository import LabelRepository
from mosemo.labels.service import segment_version
from mosemo.labels.suggestions import (
    ConfirmedExample,
    LabelCandidate,
    LabelSuggester,
    SuggestionInput,
    SuggestionResult,
    summarize_context,
)

MAX_EXAMPLES = 8
SCAN_PAGE_SIZE = 100
MAX_SCAN_CANDIDATES_PER_BATCH = 500
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Claim:
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


def _current_version(
    segment: ActivityTimelineSegment | None, *, now: datetime
) -> str | None:
    if segment is None or (
        segment.timeline_kind_at(now)
        is not ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY
    ):
        return None
    ended_at = segment.effective_ended_at(now)
    if ended_at is None:
        return None
    return segment_version(segment, ended_at=ended_at)


class RecentConfirmedExampleRetriever:
    """Find recent confirmations that still apply to the current suggestion input."""

    PAGE_SIZE = 40

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(
        self,
        *,
        account_id: UUID,
        exclude_first_event_id: UUID,
        active_label_ids: set[UUID],
        now: datetime,
    ) -> list[ConfirmedExample]:
        query = (
            select(ActivityLabelConfirmation, ActivityTimelineSegment)
            .join(
                ActivityTimelineSegment,
                and_(
                    ActivityTimelineSegment.account_id
                    == ActivityLabelConfirmation.account_id,
                    ActivityTimelineSegment.first_event_id
                    == ActivityLabelConfirmation.first_event_id,
                ),
            )
            .where(ActivityLabelConfirmation.account_id == account_id)
            .where(ActivityLabelConfirmation.first_event_id != exclude_first_event_id)
            .order_by(
                ActivityLabelConfirmation.updated_at.desc(),
                ActivityLabelConfirmation.confirmation_id.desc(),
            )
        )
        examples: list[ConfirmedExample] = []
        offset = 0
        while len(examples) < MAX_EXAMPLES:
            rows = (
                await self._session.execute(query.limit(self.PAGE_SIZE).offset(offset))
            ).all()
            if not rows:
                break
            for confirmation, segment in rows:
                if not self._is_usable_as_suggestion_example(
                    confirmation,
                    segment,
                    active_label_ids=active_label_ids,
                    now=now,
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
        segment: ActivityTimelineSegment,
        *,
        active_label_ids: set[UUID],
        now: datetime,
    ) -> bool:
        return _current_version(segment, now=now) == confirmation.segment_version and (
            confirmation.label_id is None or confirmation.label_id in active_label_ids
        )


class ProposalProcessor:
    """Process a candidate independently of whether a DB poller or queue found it."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        suggester: LabelSuggester,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._suggester = suggester
        self._now = now or (lambda: datetime.now(UTC))

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
            account_id=account_id,
            first_event_id=first_event_id,
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
        now = self._now()
        async with self._session_factory() as session:
            label_repository = LabelRepository(session)
            labels = await label_repository.list_active_owned(account_id=account_id)
            examples = await RecentConfirmedExampleRetriever(session).find(
                account_id=account_id,
                exclude_first_event_id=first_event_id,
                active_label_ids={label.label_id for label in labels},
                now=now,
            )
        return SuggestionInput(
            activity=activity,
            labels=tuple(
                LabelCandidate(label.label_id, label.display_name) for label in labels
            ),
            examples=tuple(examples),
        )

    async def _claim(self, account_id: UUID, first_event_id: UUID) -> _Claim:
        now = self._now()
        async with self._session_factory() as session, session.begin():
            await acquire_activity_timeline_lock(session, account_id=account_id)
            activity_repository = ActivityRepository(session)
            label_repository = LabelRepository(session)
            segment = await activity_repository.find_account_segment_by_first_event(
                account_id=account_id, first_event_id=first_event_id
            )
            version = _current_version(segment, now=now)
            if segment is None or version is None:
                raise NoProposalWorkError
            confirmation = await label_repository.find_confirmation(
                account_id=account_id, first_event_id=first_event_id
            )
            if confirmation is not None and confirmation.segment_version == version:
                raise NoProposalWorkError
            proposal = await label_repository.find_proposal(
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
                session.add(proposal)

            token = uuid4()
            claim_result = proposal.try_start_attempt(now=now, token=token)
            if claim_result is ProposalClaimResult.CLAIMED:
                assert segment.context is not None
                await session.flush()
                return _Claim(
                    proposal.proposal_id,
                    token,
                    version,
                    summarize_context(segment.context),
                )

        # Commit any expired final-lease cleanup before signaling no work.
        raise NoProposalWorkError

    async def _finish(
        self,
        *,
        account_id: UUID,
        first_event_id: UUID,
        claim: _Claim,
        request: SuggestionInput | None,
        result: SuggestionResult | None,
        latency_ms: int,
    ) -> None:
        now = self._now()
        async with self._session_factory() as session, session.begin():
            await acquire_activity_timeline_lock(session, account_id=account_id)
            proposal = await session.get(ActivityLabelProposal, claim.proposal_id)
            if proposal is None or not proposal.owns_attempt(claim.lease_token):
                return
            label_repository = LabelRepository(session)
            segment = await ActivityRepository(
                session
            ).find_account_segment_by_first_event(
                account_id=account_id, first_event_id=first_event_id
            )
            confirmation = await label_repository.find_confirmation(
                account_id=account_id, first_event_id=first_event_id
            )
            if _current_version(segment, now=now) != claim.segment_version or (
                confirmation is not None
                and confirmation.segment_version == claim.segment_version
            ):
                proposal.supersede_attempt(token=claim.lease_token)
            elif result is None or (
                request is None
                or (
                    result.label_id is not None
                    and (
                        result.label_id
                        not in {label.label_id for label in request.labels}
                        or await label_repository.find_active_owned(
                            account_id=account_id, label_id=result.label_id
                        )
                        is None
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
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._processor = processor
        self._now = now or (lambda: datetime.now(UTC))
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
                return ProposalScanBatch(processed, scanned, True)

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
        return ProposalScanBatch(processed, scanned, False)

    async def _find_page(self, *, limit: int) -> list[_ScannedSegment]:
        now = self._now()
        async with self._session_factory() as session:
            statement = (
                select(ActivityTimelineSegment)
                .where(ActivityTimelineSegment.segment_type == SegmentType.ACTIVITY)
                .where(ActivityTimelineSegment.context["kind"].astext == "detailed")
                .where(
                    or_(
                        ActivityTimelineSegment.ended_at.is_not(None),
                        ActivityTimelineSegment.last_observed_at
                        < now - MAX_OBSERVATION_GAP,
                    )
                )
                .order_by(ActivityTimelineSegment.segment_id)
                .limit(limit)
            )
            if self._last_segment_id is not None:
                statement = statement.where(
                    ActivityTimelineSegment.segment_id > self._last_segment_id
                )
            segments = list((await session.scalars(statement)).all())
            versioned_segments = [
                (segment, _current_version(segment, now=now)) for segment in segments
            ]
            current_segments = [
                (segment, version)
                for segment, version in versioned_segments
                if version is not None
            ]
            if not current_segments:
                return [
                    _ScannedSegment(
                        segment.segment_id,
                        segment.account_id,
                        segment.first_event_id,
                        False,
                    )
                    for segment in segments
                ]

            confirmation_keys = [
                (segment.account_id, segment.first_event_id)
                for segment, _ in current_segments
            ]
            confirmations = (
                await session.scalars(
                    select(ActivityLabelConfirmation).where(
                        tuple_(
                            ActivityLabelConfirmation.account_id,
                            ActivityLabelConfirmation.first_event_id,
                        ).in_(confirmation_keys)
                    )
                )
            ).all()
            current_confirmations = {
                (
                    confirmation.account_id,
                    confirmation.first_event_id,
                ): confirmation.segment_version
                for confirmation in confirmations
            }

            proposal_keys = [
                (segment.account_id, segment.first_event_id, version)
                for segment, version in current_segments
            ]
            proposals = (
                await session.scalars(
                    select(ActivityLabelProposal).where(
                        tuple_(
                            ActivityLabelProposal.account_id,
                            ActivityLabelProposal.first_event_id,
                            ActivityLabelProposal.segment_version,
                        ).in_(proposal_keys)
                    )
                )
            ).all()
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
                    confirmation_version = current_confirmations.get(
                        (segment.account_id, segment.first_event_id)
                    )
                    proposal = current_proposals.get(
                        (segment.account_id, segment.first_event_id, version)
                    )
                    needs_processing = confirmation_version != version and (
                        proposal is None or proposal.needs_worker_attention(now)
                    )
                page.append(
                    _ScannedSegment(
                        segment.segment_id,
                        segment.account_id,
                        segment.first_event_id,
                        needs_processing,
                    )
                )
            return page

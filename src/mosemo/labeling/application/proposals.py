import logging
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.infra.transactions import read_snapshot, reset_session, unit_of_work
from mosemo.labeling.application.ports import LabelSuggester
from mosemo.labeling.domain.confirmations import ActivityLabelConfirmation
from mosemo.labeling.domain.proposals import (
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
    ProposalClaimResult,
    ProposalCompletion,
)
from mosemo.labeling.domain.suggestions import (
    ConfirmedExample,
    LabelCandidate,
    SuggestionInput,
    SuggestionResult,
)
from mosemo.labeling.infrastructure.confirmations import ConfirmationRepository
from mosemo.labeling.infrastructure.proposals import ProposalRepository
from mosemo.labels.public import LabelCatalog
from mosemo.observation.public import (
    ActivityTimelineBusyError,
    ActivityTimelineKind,
    BrowserWebContext,
    CapturedString,
    CapturedWindow,
    DetailedActivityContext,
    ObservationTimeline,
    ObservedSegment,
)
from mosemo.shared.application.clock import Clock
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.application.use_case import UseCase

MAX_EXAMPLES = 8
SCAN_PAGE_SIZE = 100
MAX_SCAN_CANDIDATES_PER_BATCH = 500
logger = logging.getLogger(__name__)
_context_adapter = TypeAdapter(DetailedActivityContext)


def summarize_context(context: dict[str, object]) -> str:
    """The suggester's view of a detailed context; a URL keeps only its host."""
    detailed = _context_adapter.validate_python(context)
    parts: list[str] = []
    if isinstance(detailed.app.bundle_id, CapturedString):
        parts.append(f"app_bundle_id: {detailed.app.bundle_id.value[:256]}")
    if isinstance(detailed.app.name, CapturedString):
        parts.append(f"app_name: {detailed.app.name.value[:256]}")
    if isinstance(detailed.window, CapturedWindow):
        parts.append(f"window_title: {detailed.window.title.value[:256]}")
    if isinstance(detailed.web, BrowserWebContext):
        if isinstance(detailed.web.tab_title, CapturedString):
            parts.append(f"tab_title: {detailed.web.tab_title.value[:256]}")
        if isinstance(detailed.web.url, CapturedString):
            try:
                host = urlsplit(detailed.web.url.value).hostname
            except ValueError:
                host = None
            if host:
                parts.append(f"web_host: {host[:256]}")
    return "\n".join(parts)


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


class NoProposalWorkError(Exception):
    """No current proposal attempt can be claimed for this candidate."""


@dataclass(frozen=True)
class ProposalClaim:
    account_id: UUID
    first_event_id: UUID
    proposal_id: UUID
    lease_token: UUID
    segment_version: str
    activity: str


class RecentConfirmedExampleRetriever:
    """Find recent confirmations that still apply to the current suggestion input."""

    PAGE_SIZE = 40

    def __init__(
        self, *, confirmations: ConfirmationRepository, timeline: ObservationTimeline
    ) -> None:
        self._confirmations = confirmations
        self._timeline = timeline

    async def find(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        exclude_first_event_id: UUID,
        active_label_ids: set[UUID],
        now: datetime,
    ) -> list[ConfirmedExample]:
        examples: list[ConfirmedExample] = []
        offset = 0
        while len(examples) < MAX_EXAMPLES:
            rows = await self._confirmations.list_example_page(
                db_session,
                account_id=account_id,
                exclude_first_event_id=exclude_first_event_id,
                limit=self.PAGE_SIZE,
                offset=offset,
            )
            if not rows:
                break
            segments = await self._timeline.find_by_first_events(
                db_session,
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
                        confirmation_id=confirmation.id,
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


class SuggestionInputs:
    """Builds the suggester input from the account's active labels and examples."""

    def __init__(
        self, *, labels: LabelCatalog, examples: RecentConfirmedExampleRetriever
    ) -> None:
        self._labels = labels
        self._examples = examples

    async def build(
        self, db_session: AsyncSession, *, claim: ProposalClaim, now: datetime
    ) -> SuggestionInput:
        labels = await self._labels.list_active(db_session, claim.account_id)
        examples = await self._examples.find(
            db_session,
            account_id=claim.account_id,
            exclude_first_event_id=claim.first_event_id,
            active_label_ids={label.id for label in labels},
            now=now,
        )
        return SuggestionInput(
            activity=claim.activity,
            labels=tuple(
                LabelCandidate(label.id, label.display_name) for label in labels
            ),
            examples=tuple(examples),
        )


class ProposalAttempts:
    """Claims and finishes proposal attempts inside the use case transaction.

    The caller holds the account timeline lock, so the segment version cannot
    change between the checks and the write.
    """

    def __init__(
        self,
        *,
        confirmations: ConfirmationRepository,
        proposals: ProposalRepository,
        labels: LabelCatalog,
        timeline: ObservationTimeline,
    ) -> None:
        self._confirmations = confirmations
        self._proposals = proposals
        self._labels = labels
        self._timeline = timeline

    async def claim(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        first_event_id: UUID,
        now: datetime,
    ) -> ProposalClaim | None:
        """Start an attempt on the current version, or `None` when there is no work.

        A final attempt whose lease expired is recorded as failed even when it
        returns `None`, so the caller commits in both cases.
        """
        await self._timeline.lock(db_session, account_id)
        segment = await self._timeline.find_by_first_event(
            db_session, account_id=account_id, first_event_id=first_event_id, now=now
        )
        version = _current_version(segment)
        if segment is None or version is None:
            return None
        confirmation = await self._confirmations.find(
            db_session, account_id=account_id, first_event_id=first_event_id
        )
        if confirmation is not None and confirmation.applies_to(version):
            return None
        proposal = await self._proposals.find(
            db_session,
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
            self._proposals.add(db_session, proposal)

        token = uuid4()
        if proposal.try_start_attempt(now=now, token=token) is not (
            ProposalClaimResult.CLAIMED
        ):
            return None
        assert segment.context is not None
        return ProposalClaim(
            account_id=account_id,
            first_event_id=first_event_id,
            proposal_id=proposal.id,
            lease_token=token,
            segment_version=version,
            activity=summarize_context(segment.context),
        )

    async def finish(
        self,
        db_session: AsyncSession,
        *,
        claim: ProposalClaim,
        outcome: tuple[SuggestionInput, SuggestionResult, int] | None,
        now: datetime,
    ) -> None:
        """Record the attempt's outcome if the claim still owns it.

        `outcome` is the input, the result and the model latency in
        milliseconds, or `None` when the suggestion failed.
        """
        account_id = claim.account_id
        await self._timeline.lock(db_session, account_id)
        proposal = await self._proposals.find_by_id(db_session, claim.proposal_id)
        if proposal is None or not proposal.owns_attempt(claim.lease_token):
            return
        segment = await self._timeline.find_by_first_event(
            db_session,
            account_id=account_id,
            first_event_id=claim.first_event_id,
            now=now,
        )
        confirmation = await self._confirmations.find(
            db_session, account_id=account_id, first_event_id=claim.first_event_id
        )
        if _current_version(segment) != claim.segment_version or (
            confirmation is not None and confirmation.applies_to(claim.segment_version)
        ):
            proposal.supersede_attempt(token=claim.lease_token)
        elif outcome is None or not await self._suggests_a_candidate(
            db_session, account_id=account_id, request=outcome[0], result=outcome[1]
        ):
            proposal.fail_attempt(token=claim.lease_token, now=now)
        else:
            request, result, latency_ms = outcome
            proposal.complete_attempt(
                token=claim.lease_token,
                completion=ProposalCompletion(
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
                ),
                now=now,
            )

    async def _suggests_a_candidate(
        self,
        db_session: AsyncSession,
        *,
        account_id: UUID,
        request: SuggestionInput,
        result: SuggestionResult,
    ) -> bool:
        """Unclassified, or a label that was offered and is still active."""
        return result.label_id is None or (
            result.label_id in {label.label_id for label in request.labels}
            and await self._labels.is_active(
                db_session, account_id=account_id, label_id=result.label_id
            )
        )


class ProposeLabel(UseCase):
    """Process one candidate segment, whether a DB poller or a queue found it."""

    def __init__(
        self,
        *,
        attempts: ProposalAttempts,
        inputs: SuggestionInputs,
        suggester: LabelSuggester,
        dispatcher: EventDispatcher,
        clock: Clock,
    ) -> None:
        self._attempts = attempts
        self._inputs = inputs
        self._suggester = suggester
        self._dispatcher = dispatcher
        self._clock = clock

    async def execute(
        self, db_session: AsyncSession, *, account_id: UUID, first_event_id: UUID
    ) -> bool:
        """Claim, ask the suggester outside any transaction, then save the outcome.

        Returns whether an attempt was made. Raises observation's
        `ActivityTimelineBusyError` when the timeline lock is not acquired.
        """
        try:
            claim = await self._claim(
                db_session, account_id=account_id, first_event_id=first_event_id
            )
        except NoProposalWorkError:
            return False

        request: SuggestionInput | None = None
        model_started: float | None = None
        try:
            reset_session(db_session)
            async with read_snapshot(db_session):
                request = await self._inputs.build(
                    db_session, claim=claim, now=self._clock()
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
        now = self._clock()
        reset_session(db_session)
        async with unit_of_work(db_session, self._dispatcher):
            await self._attempts.finish(
                db_session,
                claim=claim,
                outcome=(
                    None
                    if request is None or result is None
                    else (request, result, latency_ms)
                ),
                now=now,
            )
        return True

    async def _claim(
        self, db_session: AsyncSession, *, account_id: UUID, first_event_id: UUID
    ) -> ProposalClaim:
        now = self._clock()
        reset_session(db_session)
        async with unit_of_work(db_session, self._dispatcher):
            claim = await self._attempts.claim(
                db_session,
                account_id=account_id,
                first_event_id=first_event_id,
                now=now,
            )
        # Raised after the commit so an expired final lease is still recorded.
        if claim is None:
            raise NoProposalWorkError
        return claim


@dataclass(frozen=True)
class ProposalScanBatch:
    """`last_segment_id` is the cursor for the next batch; `None` restarts it."""

    processed: int
    scanned: int
    sweep_complete: bool
    last_segment_id: UUID | None = None


@dataclass(frozen=True)
class _ScannedSegment:
    segment_id: UUID
    account_id: UUID
    first_event_id: UUID
    needs_processing: bool


class ScanLabelProposals(UseCase):
    """One bounded batch of the sweep over closed segments, by segment ID."""

    def __init__(
        self,
        *,
        propose: ProposeLabel,
        confirmations: ConfirmationRepository,
        proposals: ProposalRepository,
        timeline: ObservationTimeline,
        clock: Clock,
    ) -> None:
        self._propose = propose
        self._confirmations = confirmations
        self._proposals = proposals
        self._timeline = timeline
        self._clock = clock

    async def execute(
        self,
        db_session: AsyncSession,
        *,
        after_segment_id: UUID | None = None,
        max_processed: int = 100,
        max_scanned: int = MAX_SCAN_CANDIDATES_PER_BATCH,
    ) -> ProposalScanBatch:
        """Scan segments after the cursor and propose labels for those that need it.

        A candidate whose timeline lock is busy is skipped until the next sweep.
        """
        processed = 0
        scanned = 0
        last_segment_id = after_segment_id
        while processed < max_processed and scanned < max_scanned:
            page = await self._find_page(
                db_session,
                after_segment_id=last_segment_id,
                limit=min(SCAN_PAGE_SIZE, max_scanned - scanned),
            )
            if not page:
                return ProposalScanBatch(processed, scanned, sweep_complete=True)
            for segment in page:
                scanned += 1
                last_segment_id = segment.segment_id
                if segment.needs_processing and await self._process(
                    db_session, segment
                ):
                    processed += 1
                    if processed == max_processed:
                        break
        return ProposalScanBatch(
            processed, scanned, sweep_complete=False, last_segment_id=last_segment_id
        )

    async def _process(
        self, db_session: AsyncSession, segment: _ScannedSegment
    ) -> bool:
        try:
            return await self._propose.execute(
                db_session,
                account_id=segment.account_id,
                first_event_id=segment.first_event_id,
            )
        except ActivityTimelineBusyError:
            return False

    async def _find_page(
        self,
        db_session: AsyncSession,
        *,
        after_segment_id: UUID | None,
        limit: int,
    ) -> list[_ScannedSegment]:
        now = self._clock()
        reset_session(db_session)
        async with read_snapshot(db_session):
            segments = await self._timeline.list_closed_page(
                db_session, after_segment_id=after_segment_id, now=now, limit=limit
            )
            versioned = [(segment, _current_version(segment)) for segment in segments]
            current = [
                (segment, version)
                for segment, version in versioned
                if version is not None
            ]
            confirmations: dict[tuple[UUID, UUID], ActivityLabelConfirmation] = {}
            proposals: dict[tuple[UUID, UUID, str], ActivityLabelProposal] = {}
            if current:
                confirmations = {
                    (confirmation.account_id, confirmation.first_event_id): (
                        confirmation
                    )
                    for confirmation in await self._confirmations.list_by_keys(
                        db_session,
                        [
                            (segment.account_id, segment.first_event_id)
                            for segment, _ in current
                        ],
                    )
                }
                proposals = {
                    (
                        proposal.account_id,
                        proposal.first_event_id,
                        proposal.segment_version,
                    ): proposal
                    for proposal in await self._proposals.list_by_keys(
                        db_session,
                        [
                            (segment.account_id, segment.first_event_id, version)
                            for segment, version in current
                        ],
                    )
                }
        return [
            _ScannedSegment(
                segment.id,
                segment.account_id,
                segment.first_event_id,
                needs_processing=version is not None
                and _needs_processing(
                    confirmations.get((segment.account_id, segment.first_event_id)),
                    proposals.get(
                        (segment.account_id, segment.first_event_id, version)
                    ),
                    version=version,
                    now=now,
                ),
            )
            for segment, version in versioned
        ]


class SweepLabelProposals(UseCase):
    """Scan every closed segment from the start, batch by batch."""

    def __init__(self, *, scan: ScanLabelProposals) -> None:
        self._scan = scan

    async def execute(
        self,
        db_session: AsyncSession,
        *,
        max_scanned: int = MAX_SCAN_CANDIDATES_PER_BATCH,
    ) -> int:
        """Return the number of proposal attempts made.

        The segment cursor only moves forward, so the sweep completes unless new
        closed segments keep arriving faster than they are processed.
        """
        processed = 0
        cursor: UUID | None = None
        while True:
            batch = await self._scan.execute(
                db_session, after_segment_id=cursor, max_scanned=max_scanned
            )
            processed += batch.processed
            if batch.sweep_complete:
                return processed
            cursor = batch.last_segment_id

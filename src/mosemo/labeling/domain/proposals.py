from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, StrEnum, auto
from uuid import UUID, uuid7

from mosemo.shared.domain.aggregates import AggregateRoot

PROPOSAL_RETRY_DELAYS = (timedelta(minutes=1), timedelta(minutes=5))
MAX_PROPOSAL_ATTEMPTS = len(PROPOSAL_RETRY_DELAYS) + 1
PROPOSAL_LEASE_DURATION = timedelta(minutes=2)


class ActivityLabelProposalStatus(StrEnum):
    PROCESSING = "processing"
    FAILED = "failed"
    READY = "ready"
    SUPERSEDED = "superseded"


class ProposalClaimResult(Enum):
    CLAIMED = auto()
    WAITING = auto()
    TERMINAL = auto()


@dataclass(frozen=True)
class ProposalCompletion:
    label_id: UUID | None
    provider: str
    model: str
    prompt_version: str
    retrieved_example_ids: tuple[str, ...]
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


@dataclass(eq=False, repr=False, kw_only=True)
class ActivityLabelProposal(AggregateRoot):
    """The AI label proposal for one version of an observed segment."""

    id: UUID = field(default_factory=uuid7)
    account_id: UUID
    first_event_id: UUID
    segment_version: str
    status: ActivityLabelProposalStatus
    suggested_label_id: UUID | None = None
    attempt_count: int = 0
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    retrieved_example_ids: list[str] | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    suggested_at: datetime | None = None
    created_at: datetime = field(init=False)
    updated_at: datetime = field(init=False)

    def try_start_attempt(self, *, now: datetime, token: UUID) -> ProposalClaimResult:
        if self.status in {
            ActivityLabelProposalStatus.READY,
            ActivityLabelProposalStatus.SUPERSEDED,
        }:
            return ProposalClaimResult.TERMINAL

        if self.attempt_count >= MAX_PROPOSAL_ATTEMPTS:
            if self.has_active_lease(now):
                return ProposalClaimResult.WAITING
            if self.status is ActivityLabelProposalStatus.PROCESSING:
                self._record_failure(now)
            return ProposalClaimResult.TERMINAL

        if self.has_active_lease(now) or (
            self.status is ActivityLabelProposalStatus.FAILED
            and self.next_attempt_at is not None
            and self.next_attempt_at > now
        ):
            return ProposalClaimResult.WAITING

        self.status = ActivityLabelProposalStatus.PROCESSING
        self.attempt_count += 1
        self.lease_token = token
        self.lease_expires_at = now + PROPOSAL_LEASE_DURATION
        self.next_attempt_at = None
        return ProposalClaimResult.CLAIMED

    def has_active_lease(self, now: datetime) -> bool:
        return (
            self.status is ActivityLabelProposalStatus.PROCESSING
            and self.lease_expires_at is not None
            and self.lease_expires_at > now
        )

    def has_expired_lease(self, now: datetime) -> bool:
        return (
            self.status is ActivityLabelProposalStatus.PROCESSING
            and self.lease_expires_at is not None
            and self.lease_expires_at <= now
        )

    def owns_attempt(self, token: UUID) -> bool:
        return self._is_owned_by(token)

    def needs_worker_attention(self, now: datetime) -> bool:
        if self.status in {
            ActivityLabelProposalStatus.READY,
            ActivityLabelProposalStatus.SUPERSEDED,
        }:
            return False
        if self.status is ActivityLabelProposalStatus.PROCESSING:
            return not self.has_active_lease(now)
        return self.attempt_count < MAX_PROPOSAL_ATTEMPTS and (
            self.next_attempt_at is None or self.next_attempt_at <= now
        )

    def fail_attempt(self, *, token: UUID, now: datetime) -> bool:
        if not self._is_owned_by(token):
            return False
        self._record_failure(now)
        return True

    def supersede_attempt(self, *, token: UUID) -> bool:
        if not self._is_owned_by(token):
            return False
        self.status = ActivityLabelProposalStatus.SUPERSEDED
        self._clear_lease()
        return True

    def complete_attempt(
        self, *, token: UUID, completion: ProposalCompletion, now: datetime
    ) -> bool:
        if not self._is_owned_by(token):
            return False
        self.status = ActivityLabelProposalStatus.READY
        self.suggested_label_id = completion.label_id
        self.provider = completion.provider
        self.model = completion.model
        self.prompt_version = completion.prompt_version
        self.retrieved_example_ids = list(completion.retrieved_example_ids)
        self.latency_ms = completion.latency_ms
        self.input_tokens = completion.input_tokens
        self.output_tokens = completion.output_tokens
        self.suggested_at = now
        self._clear_lease()
        return True

    def _is_owned_by(self, token: UUID) -> bool:
        return (
            self.status is ActivityLabelProposalStatus.PROCESSING
            and self.lease_token == token
        )

    def _record_failure(self, now: datetime) -> None:
        self.status = ActivityLabelProposalStatus.FAILED
        self.next_attempt_at = (
            now + PROPOSAL_RETRY_DELAYS[self.attempt_count - 1]
            if self.attempt_count < MAX_PROPOSAL_ATTEMPTS
            else None
        )
        self._clear_lease()

    def _clear_lease(self) -> None:
        self.lease_token = None
        self.lease_expires_at = None

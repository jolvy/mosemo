from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, StrEnum, auto
from uuid import UUID, uuid7

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as OrmEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mosemo.database import Base

DEFAULT_LABEL_NAMES: tuple[str, ...] = ("코딩", "학습", "소통", "쇼핑", "여가")
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


class Label(Base):
    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("account_id", "display_name"),)

    label_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
    )
    display_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ActivityLabelConfirmation(Base):
    __tablename__ = "activity_label_confirmations"
    __table_args__ = (
        UniqueConstraint("account_id", "first_event_id"),
        Index(
            "activity_label_confirmations_account_id_idx",
            "account_id",
        ),
    )

    confirmation_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
    )
    first_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("activity_records.event_id", ondelete="CASCADE")
    )
    segment_version: Mapped[str] = mapped_column(String(64))
    label_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("labels.label_id", ondelete="NO ACTION"),
        nullable=True,
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )


class ActivityLabelProposal(Base):
    __tablename__ = "activity_label_proposals"
    __table_args__ = (
        UniqueConstraint("account_id", "first_event_id", "segment_version"),
        CheckConstraint("attempt_count BETWEEN 0 AND 3", name="attempt_count"),
        Index("activity_label_proposals_account_id_idx", "account_id"),
    )

    proposal_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE")
    )
    first_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("activity_records.event_id", ondelete="CASCADE")
    )
    segment_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[ActivityLabelProposalStatus] = mapped_column(
        OrmEnum(
            ActivityLabelProposalStatus,
            values_callable=lambda members: [member.value for member in members],
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=16,
        )
    )
    suggested_label_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("labels.label_id", ondelete="NO ACTION"), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retrieved_example_ids: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

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

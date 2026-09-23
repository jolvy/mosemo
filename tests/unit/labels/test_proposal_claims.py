from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mosemo.labels.models import (
    ActivityLabelProposal,
    ActivityLabelProposalStatus,
    ProposalClaimResult,
    ProposalCompletion,
)

NOW = datetime(2026, 9, 24, tzinfo=UTC)


def make_proposal(
    *,
    status: ActivityLabelProposalStatus,
    attempt_count: int,
    lease_expires_at: datetime | None = None,
    next_attempt_at: datetime | None = None,
) -> ActivityLabelProposal:
    return ActivityLabelProposal(
        account_id=uuid4(),
        first_event_id=uuid4(),
        segment_version="version",
        status=status,
        attempt_count=attempt_count,
        lease_expires_at=lease_expires_at,
        next_attempt_at=next_attempt_at,
    )


def test_try_start_attempt_claims_and_records_lease() -> None:
    proposal = make_proposal(
        status=ActivityLabelProposalStatus.FAILED,
        attempt_count=1,
        next_attempt_at=NOW,
    )
    token = uuid4()

    result = proposal.try_start_attempt(now=NOW, token=token)

    assert result is ProposalClaimResult.CLAIMED
    assert proposal.status is ActivityLabelProposalStatus.PROCESSING
    assert proposal.attempt_count == 2
    assert proposal.lease_token == token
    assert proposal.lease_expires_at == NOW + timedelta(minutes=2)
    assert proposal.next_attempt_at is None


@pytest.mark.parametrize(
    ("status", "attempt_count", "lease_expires_at", "next_attempt_at"),
    [
        (
            ActivityLabelProposalStatus.PROCESSING,
            1,
            NOW + timedelta(seconds=1),
            None,
        ),
        (
            ActivityLabelProposalStatus.FAILED,
            1,
            None,
            NOW + timedelta(seconds=1),
        ),
        (
            ActivityLabelProposalStatus.PROCESSING,
            3,
            NOW + timedelta(seconds=1),
            None,
        ),
    ],
)
def test_try_start_attempt_waits_for_live_lease_or_retry_time(
    status: ActivityLabelProposalStatus,
    attempt_count: int,
    lease_expires_at: datetime | None,
    next_attempt_at: datetime | None,
) -> None:
    proposal = make_proposal(
        status=status,
        attempt_count=attempt_count,
        lease_expires_at=lease_expires_at,
        next_attempt_at=next_attempt_at,
    )
    old_token = uuid4()
    proposal.lease_token = old_token

    result = proposal.try_start_attempt(now=NOW, token=uuid4())

    assert result is ProposalClaimResult.WAITING
    assert proposal.status is status
    assert proposal.attempt_count == attempt_count
    assert proposal.lease_token == old_token


def test_exhausted_expired_lease_becomes_failed_and_terminal() -> None:
    proposal = make_proposal(
        status=ActivityLabelProposalStatus.PROCESSING,
        attempt_count=3,
        lease_expires_at=NOW,
    )
    proposal.lease_token = uuid4()

    result = proposal.try_start_attempt(now=NOW, token=uuid4())

    assert result is ProposalClaimResult.TERMINAL
    assert proposal.status is ActivityLabelProposalStatus.FAILED
    assert proposal.lease_token is None
    assert proposal.lease_expires_at is None


@pytest.mark.parametrize(
    "status",
    [ActivityLabelProposalStatus.READY, ActivityLabelProposalStatus.SUPERSEDED],
)
def test_completed_proposal_is_terminal(status: ActivityLabelProposalStatus) -> None:
    proposal = make_proposal(status=status, attempt_count=1)

    result = proposal.try_start_attempt(now=NOW, token=uuid4())

    assert result is ProposalClaimResult.TERMINAL
    assert proposal.status is status
    assert proposal.attempt_count == 1


@pytest.mark.parametrize(
    ("attempt_count", "retry_delay"),
    [
        (1, timedelta(minutes=1)),
        (2, timedelta(minutes=5)),
        (3, None),
    ],
)
def test_failure_records_retry_time_and_releases_current_lease(
    attempt_count: int, retry_delay: timedelta | None
) -> None:
    proposal = make_proposal(
        status=ActivityLabelProposalStatus.PROCESSING,
        attempt_count=attempt_count,
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    token = uuid4()
    proposal.lease_token = token

    assert proposal.fail_attempt(token=token, now=NOW)

    assert proposal.status is ActivityLabelProposalStatus.FAILED
    assert proposal.next_attempt_at == (NOW + retry_delay if retry_delay else None)
    assert proposal.lease_token is None
    assert proposal.lease_expires_at is None


def test_completion_records_result_and_releases_current_lease() -> None:
    proposal = make_proposal(
        status=ActivityLabelProposalStatus.PROCESSING,
        attempt_count=1,
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    token = uuid4()
    label_id = uuid4()
    proposal.lease_token = token
    completion = ProposalCompletion(
        label_id=label_id,
        provider="test-provider",
        model="test-provider:model",
        prompt_version="v1",
        retrieved_example_ids=(str(uuid4()),),
        latency_ms=123,
        input_tokens=45,
        output_tokens=6,
    )

    assert proposal.complete_attempt(token=token, completion=completion, now=NOW)

    assert proposal.status is ActivityLabelProposalStatus.READY
    assert proposal.suggested_label_id == label_id
    assert proposal.provider == "test-provider"
    assert proposal.model == "test-provider:model"
    assert proposal.prompt_version == "v1"
    assert proposal.retrieved_example_ids == list(completion.retrieved_example_ids)
    assert proposal.latency_ms == 123
    assert proposal.input_tokens == 45
    assert proposal.output_tokens == 6
    assert proposal.suggested_at == NOW
    assert proposal.lease_token is None
    assert proposal.lease_expires_at is None


def test_stale_attempt_cannot_finish_after_a_new_lease_was_claimed() -> None:
    proposal = make_proposal(
        status=ActivityLabelProposalStatus.PROCESSING,
        attempt_count=2,
        lease_expires_at=NOW + timedelta(minutes=2),
    )
    current_token = uuid4()
    proposal.lease_token = current_token
    completion = ProposalCompletion(
        label_id=None,
        provider="test-provider",
        model="test-provider:model",
        prompt_version="v1",
        retrieved_example_ids=(),
        latency_ms=1,
        input_tokens=None,
        output_tokens=None,
    )

    assert not proposal.complete_attempt(token=uuid4(), completion=completion, now=NOW)

    assert proposal.status is ActivityLabelProposalStatus.PROCESSING
    assert proposal.lease_token == current_token
    assert proposal.suggested_at is None


@pytest.mark.parametrize(
    ("status", "attempt_count", "lease_expires_at", "next_attempt_at", "expected"),
    [
        (ActivityLabelProposalStatus.READY, 1, None, None, False),
        (ActivityLabelProposalStatus.SUPERSEDED, 1, None, None, False),
        (
            ActivityLabelProposalStatus.PROCESSING,
            1,
            NOW + timedelta(seconds=1),
            None,
            False,
        ),
        (ActivityLabelProposalStatus.PROCESSING, 1, NOW, None, True),
        (
            ActivityLabelProposalStatus.FAILED,
            1,
            None,
            NOW + timedelta(seconds=1),
            False,
        ),
        (ActivityLabelProposalStatus.FAILED, 1, None, NOW, True),
        (ActivityLabelProposalStatus.FAILED, 3, None, None, False),
    ],
)
def test_needs_worker_attention_tracks_retry_and_lease_deadlines(
    status: ActivityLabelProposalStatus,
    attempt_count: int,
    lease_expires_at: datetime | None,
    next_attempt_at: datetime | None,
    expected: bool,
) -> None:
    proposal = make_proposal(
        status=status,
        attempt_count=attempt_count,
        lease_expires_at=lease_expires_at,
        next_attempt_at=next_attempt_at,
    )

    assert proposal.needs_worker_attention(NOW) is expected

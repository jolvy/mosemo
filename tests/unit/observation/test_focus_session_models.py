from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from mosemo.observation.domain.events import FocusSessionCompleted
from mosemo.observation.domain.focus_sessions import (
    FocusSession,
    FocusSessionCompletion,
    FocusSessionConflictError,
    FocusSessionInvalidTimeError,
    FocusSessionNotFoundError,
    FocusSessionPause,
)

STARTED_AT = datetime(2026, 9, 14, tzinfo=UTC)


def make_session(*, target_seconds: int = 0) -> FocusSession:
    return FocusSession(
        id=uuid4(),
        account_id=uuid4(),
        device_id=uuid4(),
        started_at=STARTED_AT,
        target_seconds=target_seconds,
    )


def test_complete_rejects_work_beyond_target() -> None:
    session = make_session(target_seconds=60)

    with pytest.raises(FocusSessionInvalidTimeError):
        session.complete(
            FocusSessionCompletion(
                ended_at=STARTED_AT + timedelta(minutes=5),
                work_seconds=61,
                label_id=uuid4(),
                description="",
            )
        )

    assert session.ended_at is None
    assert session.work_seconds is None


@pytest.mark.parametrize("owner", ["account", "device"])
def test_validate_activity_hides_sessions_owned_by_others(owner: str) -> None:
    session = make_session()

    with pytest.raises(FocusSessionNotFoundError):
        session.validate_activity(
            account_id=uuid4() if owner == "account" else session.account_id,
            device_id=uuid4() if owner == "device" else session.device_id,
            observed_at=STARTED_AT,
        )


def test_has_same_start_accepts_same_device_start_and_target() -> None:
    stored = make_session(target_seconds=60)
    candidate = FocusSession(
        id=stored.id,
        account_id=stored.account_id,
        device_id=stored.device_id,
        started_at=STARTED_AT,
        target_seconds=60,
    )

    assert stored.has_same_start(candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("device_id", uuid4()),
        ("started_at", STARTED_AT + timedelta(seconds=1)),
        ("target_seconds", 61),
    ],
)
def test_has_same_start_rejects_any_different_start_field(
    field: str, value: Any
) -> None:
    stored = make_session(target_seconds=60)
    start: dict[str, Any] = {
        "device_id": stored.device_id,
        "started_at": STARTED_AT,
        "target_seconds": 60,
    }
    candidate = FocusSession(
        id=stored.id,
        account_id=stored.account_id,
        **(start | {field: value}),
    )

    assert not stored.has_same_start(candidate)


ENDED_AT = STARTED_AT + timedelta(minutes=20)
LABEL_ID = uuid4()


def at(minutes: float) -> datetime:
    return STARTED_AT + timedelta(minutes=minutes)


def pause(start: float, end: float) -> FocusSessionPause:
    return FocusSessionPause(started_at=at(start), ended_at=at(end))


def completion(
    *, work_seconds: int = 600, pauses: tuple[FocusSessionPause, ...] | None = None
) -> FocusSessionCompletion:
    return FocusSessionCompletion(
        ended_at=ENDED_AT,
        work_seconds=work_seconds,
        label_id=LABEL_ID,
        description="",
        pauses=pauses,
    )


def spans(session: FocusSession) -> list[tuple[datetime, datetime]]:
    return [item.span() for item in session.pauses]


@pytest.mark.parametrize(
    "pauses",
    [
        pytest.param((pause(-1, 2),), id="starts-before-session"),
        pytest.param((pause(18, 21),), id="ends-after-session"),
        pytest.param((pause(2, 6), pause(5, 8)), id="overlapping"),
        pytest.param((pause(5, 4),), id="end-before-start"),
    ],
)
def test_complete_rejects_pauses_outside_the_session_or_overlapping(
    pauses: tuple[FocusSessionPause, ...],
) -> None:
    session = make_session()

    with pytest.raises(FocusSessionInvalidTimeError):
        session.complete(completion(pauses=pauses))

    assert session.ended_at is None
    assert session.events == []


def test_complete_accepts_touching_pauses_and_session_boundaries() -> None:
    session = make_session()

    session.complete(
        completion(pauses=(pause(15, 20), pause(5, 15), pause(0, 5)), work_seconds=0)
    )

    assert spans(session) == [
        pause(0, 5).span(),
        pause(5, 15).span(),
        pause(15, 20).span(),
    ]


def test_complete_drops_zero_length_pauses_and_sorts_the_rest() -> None:
    session = make_session()

    session.complete(completion(pauses=(pause(10, 12), pause(3, 3), pause(1, 2))))

    assert session.pauses_reported
    assert spans(session) == [pause(1, 2).span(), pause(10, 12).span()]


@pytest.mark.parametrize(("extra", "accepted"), [(2, True), (3, False)])
def test_complete_allows_work_up_to_two_seconds_over_the_analysable_time(
    extra: int, accepted: bool
) -> None:
    session = make_session()
    # 20 minutes minus 5 paused minutes leaves 900 analysable seconds.
    request = completion(pauses=(pause(5, 10),), work_seconds=900 + extra)

    if accepted:
        session.complete(request)
        assert session.work_seconds_gap() == -extra
    else:
        with pytest.raises(FocusSessionInvalidTimeError):
            session.complete(request)


def test_complete_without_pause_info_counts_the_whole_session() -> None:
    session = make_session()

    session.complete(completion(work_seconds=1202))

    assert not session.pauses_reported
    assert session.pauses == []
    assert session.work_seconds_gap() == -2


def test_complete_distinguishes_no_pause_info_from_no_pauses() -> None:
    without_info = make_session()
    without_pauses = make_session()

    without_info.complete(completion(pauses=None))
    without_pauses.complete(completion(pauses=(pause(4, 4),)))

    assert not without_info.pauses_reported
    assert without_pauses.pauses_reported
    assert without_pauses.pauses == []
    with pytest.raises(FocusSessionConflictError):
        without_info.complete(completion(pauses=()))
    with pytest.raises(FocusSessionConflictError):
        without_pauses.complete(completion(pauses=None))


def test_first_completion_records_one_event_and_identical_retry_records_none() -> None:
    session = make_session()
    request = completion(pauses=(pause(5, 6), pause(1, 2)))

    session.complete(request)
    # The retry sends the pauses in another order and with a zero-length one.
    session.complete(completion(pauses=(pause(1, 2), pause(7, 7), pause(5, 6))))

    assert session.events == [
        FocusSessionCompleted(
            account_id=session.account_id,
            focus_session_id=session.id,
            started_at=STARTED_AT,
            ended_at=ENDED_AT,
        )
    ]


def test_retry_with_other_pauses_conflicts() -> None:
    session = make_session()
    session.complete(completion(pauses=(pause(1, 2),)))

    with pytest.raises(FocusSessionConflictError):
        session.complete(completion(pauses=(pause(1, 3),)))

    assert spans(session) == [pause(1, 2).span()]
    assert len(session.events) == 1


def test_work_seconds_gap_requires_a_completed_session() -> None:
    with pytest.raises(ValueError, match="not completed"):
        make_session().work_seconds_gap()


def test_microsecond_pauses_leave_exact_whole_analysable_seconds() -> None:
    session = make_session()
    # 1.001 s minus 0.064 s and 0.937 s of pauses leaves exactly 0 seconds,
    # which float seconds would round to slightly below 0.
    ms = timedelta(milliseconds=1)
    request = FocusSessionCompletion(
        ended_at=STARTED_AT + 1001 * ms,
        work_seconds=2,
        label_id=LABEL_ID,
        description="",
        pauses=(
            FocusSessionPause(started_at=STARTED_AT, ended_at=STARTED_AT + 64 * ms),
            FocusSessionPause(
                started_at=STARTED_AT + 64 * ms, ended_at=STARTED_AT + 1001 * ms
            ),
        ),
    )

    session.complete(request)

    assert session.work_seconds_gap() == -2

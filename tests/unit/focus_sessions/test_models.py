from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mosemo.focus_sessions.models import (
    FocusSession,
    FocusSessionInvalidTimeError,
    FocusSessionNotFoundError,
)

STARTED_AT = datetime(2026, 9, 14, tzinfo=UTC)


def make_session(*, target_seconds: int = 0) -> FocusSession:
    return FocusSession(
        session_id=uuid4(),
        account_id=uuid4(),
        device_id=uuid4(),
        started_at=STARTED_AT,
        target_seconds=target_seconds,
    )


def test_complete_rejects_work_beyond_target() -> None:
    session = make_session(target_seconds=60)

    with pytest.raises(FocusSessionInvalidTimeError):
        session.complete(
            ended_at=STARTED_AT + timedelta(minutes=5),
            work_seconds=61,
            label_id=uuid4(),
            description="",
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
        session_id=stored.session_id,
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
    field: str, value: object
) -> None:
    stored = make_session(target_seconds=60)
    start = {
        "device_id": stored.device_id,
        "started_at": STARTED_AT,
        "target_seconds": 60,
    }
    candidate = FocusSession(
        session_id=stored.session_id,
        account_id=stored.account_id,
        **(start | {field: value}),
    )

    assert not stored.has_same_start(candidate)

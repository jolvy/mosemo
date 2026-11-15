from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, Mock, create_autospec, sentinel
from uuid import uuid4

import pytest

from mosemo.analytics.application.marking import DailyStatsMarking
from mosemo.analytics.domain.markers import MarkScope
from mosemo.analytics.infrastructure.markers import RecomputeMarkerRepository
from mosemo.identity.public import AccountDirectory
from mosemo.labeling.public import LabelConfirmed
from mosemo.observation.public import (
    ActivityTimelineKind,
    CompletedFocusSessionView,
    FocusSessionCompleted,
    FocusSessionLog,
    ObservationTimeline,
    ObservedSegment,
    TimelineChanged,
)
from mosemo.shared.domain.timezones import Timezone
from tests.support.clock import FakeClock

ACCOUNT = uuid4()
NOW = datetime(2026, 11, 10, tzinfo=UTC)
SEOUL = Timezone.ASIA_SEOUL


def kst(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC) - timedelta(hours=9)


def segment(
    kind: ActivityTimelineKind, *, ended_at: datetime | None, last: datetime | None
) -> ObservedSegment:
    return ObservedSegment(
        id=uuid4(),
        account_id=ACCOUNT,
        first_event_id=uuid4(),
        started_at=kst(1, 9),
        last_observed_at=last,
        reason=None,
        context=None,
        ended_at=ended_at,
        kind=kind,
        version=None,
    )


def completed(started_at: datetime) -> CompletedFocusSessionView:
    return CompletedFocusSessionView(
        focus_session_id=uuid4(),
        account_id=ACCOUNT,
        started_at=started_at,
        ended_at=started_at + timedelta(hours=1),
        label_id=uuid4(),
        pauses_reported=False,
        pauses=(),
    )


class Collaborators:
    def __init__(
        self,
        *,
        preceding: list[ObservedSegment] | None = None,
        sessions: list[CompletedFocusSessionView] | None = None,
    ) -> None:
        self.calls = Mock()
        self.timeline = create_autospec(ObservationTimeline, instance=True)
        self.timeline.lock = AsyncMock()
        self.timeline.list_preceding = AsyncMock(return_value=preceding or [])
        self.accounts = create_autospec(AccountDirectory, instance=True)
        self.accounts.get_timezone = AsyncMock(return_value=SEOUL)
        self.focus_sessions = create_autospec(FocusSessionLog, instance=True)
        self.focus_sessions.list_completed_overlapping = AsyncMock(
            return_value=sessions or []
        )
        self.markers = create_autospec(RecomputeMarkerRepository, instance=True)
        self.markers.upsert_source = AsyncMock()
        self.calls.attach_mock(self.timeline.lock, "lock")
        self.calls.attach_mock(self.accounts.get_timezone, "get_timezone")
        self.calls.attach_mock(self.markers.upsert_source, "upsert_source")
        self.marking = DailyStatsMarking(
            timeline=self.timeline,
            accounts=self.accounts,
            focus_sessions=self.focus_sessions,
            markers=self.markers,
            clock=FakeClock(NOW),
        )

    def marked_dates(self) -> list[date]:
        return [
            call.kwargs["local_date"]
            for call in self.markers.upsert_source.await_args_list
        ]


@pytest.mark.asyncio
async def test_locks_then_reads_timezone_then_marks_dates_in_order() -> None:
    fakes = Collaborators(sessions=[completed(kst(1, 23, 30))])

    await fakes.marking.on_timeline_changed(
        TimelineChanged(
            account_id=ACCOUNT, changed_from=kst(2, 0, 10), changed_to=kst(3, 1)
        ),
        sentinel.session,
    )

    names = [call[0] for call in fakes.calls.mock_calls]
    assert names[:2] == ["lock", "get_timezone"]
    assert set(names[2:]) == {"upsert_source"}
    assert fakes.marked_dates() == [
        date(2026, 11, 1),
        date(2026, 11, 2),
        date(2026, 11, 3),
    ]
    upsert = fakes.markers.upsert_source.await_args_list[0]
    assert upsert.kwargs == {
        "account_id": ACCOUNT,
        "local_date": date(2026, 11, 1),
        "eligible_at": MarkScope.eligible_at(date(2026, 11, 1), SEOUL),
        "now": NOW,
    }
    fakes.timeline.list_preceding.assert_awaited_once_with(
        sentinel.session, ACCOUNT, before=kst(2, 0, 10), limit=1, now=NOW
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preceding", "expected"),
    [
        # The closed activity's end date.
        (
            [
                segment(
                    ActivityTimelineKind.CLOSED_DETAILED_ACTIVITY,
                    ended_at=kst(3, 23, 55),
                    last=None,
                )
            ],
            [date(2026, 11, 3), date(2026, 11, 5)],
        ),
        # An open activity ends at its last observation.
        (
            [
                segment(
                    ActivityTimelineKind.IN_PROGRESS_ACTIVITY,
                    ended_at=None,
                    last=kst(2, 8),
                )
            ],
            [date(2026, 11, 2), date(2026, 11, 5)],
        ),
        # A capture gap or no segment adds nothing.
        (
            [segment(ActivityTimelineKind.CAPTURE_GAP, ended_at=kst(3, 1), last=None)],
            [date(2026, 11, 5)],
        ),
        ([], [date(2026, 11, 5)]),
    ],
)
async def test_timeline_change_adds_the_preceding_activity_end_date(
    preceding, expected
) -> None:
    fakes = Collaborators(preceding=preceding)

    await fakes.marking.on_timeline_changed(
        TimelineChanged(
            account_id=ACCOUNT, changed_from=kst(5, 10), changed_to=kst(5, 11)
        ),
        sentinel.session,
    )

    assert fakes.marked_dates() == expected


@pytest.mark.asyncio
async def test_label_confirmation_marks_every_overlapped_date_and_session_starts() -> (
    None
):
    fakes = Collaborators(sessions=[completed(kst(10, 23))])

    # 11-11 23:00 ~ 11-13 01:00: 11-12 has pending time of this segment too.
    await fakes.marking.on_label_confirmed(
        LabelConfirmed(
            account_id=ACCOUNT,
            segment_started_at=kst(11, 23),
            segment_ended_at=kst(13, 1),
        ),
        sentinel.session,
    )

    assert fakes.marked_dates() == [date(2026, 11, d) for d in (10, 11, 12, 13)]
    fakes.focus_sessions.list_completed_overlapping.assert_awaited_once_with(
        sentinel.session, ACCOUNT, start=kst(11, 23), end=kst(13, 1)
    )
    fakes.timeline.list_preceding.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_completion_marks_only_the_start_date() -> None:
    fakes = Collaborators()

    await fakes.marking.on_focus_session_completed(
        FocusSessionCompleted(
            account_id=ACCOUNT,
            focus_session_id=uuid4(),
            started_at=kst(1, 23, 30),
            ended_at=kst(2, 0, 40),
        ),
        sentinel.session,
    )

    assert [call[0] for call in fakes.calls.mock_calls] == [
        "lock",
        "get_timezone",
        "upsert_source",
    ]
    assert fakes.marked_dates() == [date(2026, 11, 1)]

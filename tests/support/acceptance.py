"""Opt-in acceptance time for the activity batch routes.

Both HTTP acceptance paths reject records observed more than 24 hours before
the server's acceptance time or after it. Tests that send records at fixed past
times request `acceptance_clock` and set its `now` before each request; tests
that do not request it keep the real clock. Only the acceptance clock changes:
the legacy projection clock keeps its own value.
"""

from datetime import UTC, datetime

import pytest

from mosemo.platform.clock import utc_now
from tests.support.clock import FakeClock

# Accepts the shared fixed test records, observed on 2026-09-14 from 00:00Z on.
FIXED_ACCEPTED_AT = datetime(2026, 9, 14, 23, tzinfo=UTC)


def pin_acceptance_clock(
    monkeypatch: pytest.MonkeyPatch, *use_cases: object, clock: FakeClock
) -> FakeClock:
    """Make already built acceptance use cases read `clock` for this test."""
    for use_case in use_cases:
        monkeypatch.setattr(use_case, "_acceptance_clock", clock)
    return clock


@pytest.fixture
def acceptance_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """The acceptance clock of the API's import-time `OBSERVATION` use cases.

    Starts at the real current time; set `now` (or `advance`) before a request
    to accept that request's records at another time.
    """
    from mosemo.entrypoints.api.contexts import OBSERVATION

    return pin_acceptance_clock(
        monkeypatch,
        OBSERVATION.accept_activity_batch,
        OBSERVATION.process_legacy_activity_batch,
        clock=FakeClock(utc_now()),
    )

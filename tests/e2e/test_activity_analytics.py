"""E2E-1: from an activity upload to the session analysis (verification plan
E2E-1; AC-8008, AC-8010).

The API and the Consumer run as processes started by the fixture, and every
request goes over real HTTP. The account (timezone Asia/Seoul, labels
`코딩`·`여가`) is created in the database and its access token is issued with
the test JWT key instead of Kakao. The server accepts only records observed in
the 24 hours before acceptance, and the processes' clocks cannot be pinned, so
the session lies within the last day (`upload_start`). A past date's daily
stats (AC-8015) are covered by API-5 with a pinned clock.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from mosemo.identity.bootstrap import IdentityConfig
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.labels.domain.labels import Label
from mosemo.platform.clock import utc_now
from mosemo.shared.domain.timezones import Timezone
from tests.support.e2e import SEOUL, E2eSystem, upload_start
from tests.support.identity import token_service

OBSERVATION_STEP = timedelta(seconds=30)
SESSION_END = 45
PAUSE = (30, 35)


def app_context(bundle_id: str, name: str, title: str) -> dict[str, Any]:
    return {
        "kind": "detailed",
        "app": {
            "bundleId": {"status": "captured", "value": bundle_id},
            "name": {"status": "captured", "value": name},
        },
        "window": {
            "status": "captured",
            "title": {"status": "captured", "value": title},
        },
        "web": {"kind": "not_applicable"},
    }


EDITOR = app_context("com.example.editor", "Editor", "main.py")
VIDEO = app_context("com.example.video", "Video", "Episode 1")
NOTES = app_context("com.example.notes", "Notes", "todo")
CHAT = app_context("com.example.chat", "Chat", "general")


@dataclass(frozen=True)
class Span:
    """Observations every 30 seconds over [start, end) minutes of the session."""

    name: str
    start: int
    end: int
    context: dict[str, Any]
    label: str | None


# 코딩 → 여가(B) → pending → 여가(C) → 코딩 (AC-8008), a paused visit to B, 코딩.
# The pending segment is another app: entering C through a pending segment of
# C itself would make the confirmed C no new entry.
SPANS = (
    Span("editor", 0, 10, EDITOR, "코딩"),
    Span("video", 10, 15, VIDEO, "여가"),
    Span("notes_pending", 15, 18, NOTES, None),
    Span("chat", 18, 22, CHAT, "여가"),
    Span("editor_back", 22, 30, EDITOR, "코딩"),
    Span("paused_video", 30, 35, VIDEO, "여가"),
    Span("editor_after_pause", 35, SESSION_END, EDITOR, "코딩"),
)


def minutes_us(value: float) -> int:
    return int(value * 60_000_000)


@dataclass
class Scenario:
    """One account's recent session, driven over HTTP with its own token."""

    client: httpx2.AsyncClient
    labels: dict[str, UUID]
    day: date
    start: datetime

    def at(self, minutes: float) -> datetime:
        return self.start + timedelta(minutes=minutes)

    def iso(self, minutes: float) -> str:
        return self.at(minutes).isoformat().replace("+00:00", "Z")

    async def ok(self, method: str, path: str, status: int = 200, **kwargs) -> Any:
        response = await self.client.request(method, path, **kwargs)
        assert response.status_code == status, response.text
        return response.json()

    def records(self, device_id: str, focus_session_id: str) -> list[dict]:
        """Observations of every span plus one closing observation at the end;
        the paused span carries no focus session, as the client sends it."""
        records = []
        times = [
            (span, self.at(span.start) + step * OBSERVATION_STEP)
            for span in SPANS
            for step in range((span.end - span.start) * 2)
        ]
        times.append((SPANS[-1], self.at(SESSION_END)))
        for sequence, (span, observed_at) in enumerate(times):
            record = {
                "eventId": str(uuid4()),
                "sequence": sequence,
                "recordType": "activity_observation",
                "observedAt": observed_at.isoformat().replace("+00:00", "Z"),
                "timezoneId": "Asia/Seoul",
                "utcOffsetMinutes": 540,
                "context": span.context,
            }
            if span.name != "paused_video":
                record["focusSessionId"] = focus_session_id
            records.append(record)
        return records

    async def segments(self) -> dict[str, str]:
        """Activity segment ids by span name, once all spans are segments."""
        expected = {self.iso(span.start): span.name for span in SPANS}

        async def timeline() -> dict[str, str] | None:
            items = await self.ok("GET", f"/api/v1/activities/timeline?date={self.day}")
            found = {
                expected[item["startedAt"]]: item["id"]
                for item in items
                if item["segmentType"] == "activity" and item["startedAt"] in expected
            }
            last = [item["lastObservedAt"] for item in items][-1:]
            if len(found) == len(SPANS) and last == [self.iso(SESSION_END)]:
                return found
            return None

        async with asyncio.timeout(30):
            while (found := await timeline()) is None:
                await asyncio.sleep(0.2)
        return found

    async def confirm(self, segment_id: str, label: str) -> None:
        state = await self.ok(
            "GET", f"/api/v1/activities/segments/{segment_id}/label-state"
        )
        await self.ok(
            "PUT",
            f"/api/v1/activities/segments/{segment_id}/label-confirmation",
            json={
                "segmentVersion": state["segmentVersion"],
                "selection": {"kind": "label", "labelId": str(self.labels[label])},
            },
        )


@pytest_asyncio.fixture
async def scenario(
    e2e_system: E2eSystem, e2e_database_url: str, identity_config: IdentityConfig
) -> AsyncIterator[Scenario]:
    engine = create_async_engine(e2e_database_url)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        account = Account(
            provider=AccountProvider.KAKAO, provider_subject=f"e2e-{uuid4()}"
        )
        session.add(account)
        await session.flush()
        labels = [
            Label(account_id=account.id, display_name=name) for name in ("코딩", "여가")
        ]
        session.add_all(labels)
        await session.commit()
        await session.refresh(account)
    # The day boundaries below are Seoul's: the account's default timezone.
    assert account.timezone == Timezone.ASIA_SEOUL
    token = token_service(identity_config).issue_access_token(account.id, now=utc_now())
    start = upload_start(utc_now())
    async with httpx2.AsyncClient(
        base_url=e2e_system.base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as client:
        yield Scenario(
            client=client,
            labels={label.display_name: label.id for label in labels},
            day=start.astimezone(SEOUL).date(),
            start=start,
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_e2e1_upload_to_session_analysis(scenario: Scenario) -> None:
    # 1. A registered device of an account with the labels 코딩·여가.
    device = await scenario.ok(
        "POST", "/api/v1/devices", 201, headers={"Idempotency-Key": str(uuid4())}
    )

    # 2. A session within the last day, its observations through Kafka and the
    # Consumer.
    session_id = str(uuid4())
    await scenario.ok(
        "POST",
        "/api/v1/focus-sessions",
        201,
        json={
            "id": session_id,
            "deviceId": device["id"],
            "startedAt": scenario.iso(0),
            "targetSeconds": 0,
        },
    )
    accepted = await scenario.ok(
        "POST",
        "/api/v1/activities/batches",
        202,
        json={
            "batchId": str(uuid4()),
            "deviceId": device["id"],
            "records": scenario.records(device["id"], session_id),
        },
    )
    assert accepted["status"] == "accepted"
    segments = await scenario.segments()

    # 3. Confirm every segment but the notes one, which stays pending.
    for span in SPANS:
        if span.label is not None:
            await scenario.confirm(segments[span.name], span.label)

    # 4. Complete the session with one pause.
    await scenario.ok(
        "PUT",
        f"/api/v1/focus-sessions/{session_id}/completion",
        json={
            "endedAt": scenario.iso(SESSION_END),
            "workSeconds": (SESSION_END - (PAUSE[1] - PAUSE[0])) * 60,
            "labelId": str(scenario.labels["코딩"]),
            "pauses": [
                {"startedAt": scenario.iso(PAUSE[0]), "endedAt": scenario.iso(PAUSE[1])}
            ],
        },
    )

    # 5. Session analysis.
    analysis = await scenario.ok(
        "GET", f"/api/v1/analytics/focus-sessions/{session_id}"
    )
    assert analysis["hasPauseInfo"] is True
    assert analysis["pauses"] == [
        {"startedAt": scenario.iso(PAUSE[0]), "endedAt": scenario.iso(PAUSE[1])}
    ]
    # AC-8008: one lapse; B and C entered once each; pending time is not lapse.
    # AC-8010: the paused visit to B is neither a lapse, an entry nor a transition.
    assert analysis["composition"] == {
        "analysisUs": minutes_us(40),
        "focusUs": minutes_us(28),
        "lapseUs": minutes_us(9),
        "unclassifiedUs": 0,
        "pendingUs": minutes_us(3),
        "opaqueUs": 0,
        "unobservedUs": 0,
        "lapseCount": 1,
    }
    assert [
        (row["target"]["key"], row["entries"], row["lapseUs"])
        for row in analysis["distractions"]
    ] == [
        ("com.example.video", 1, minutes_us(5)),
        ("com.example.chat", 1, minutes_us(4)),
    ]
    (lapse,) = analysis["lapses"]
    assert (lapse["startedAt"], lapse["endedAt"], lapse["returned"]) == (
        scenario.iso(10),
        scenario.iso(22),
        True,
    )
    assert lapse["lapseUs"] == minutes_us(9)
    assert lapse["transitionCount"] == 2
    # The transitions into and out of the pause (30, 35) are left out.
    assert [item["at"] for item in analysis["targetTransitions"]["items"]] == [
        scenario.iso(10),
        scenario.iso(15),
        scenario.iso(18),
        scenario.iso(22),
    ]

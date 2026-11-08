"""Deterministic load-test seed data and its loader.

The generator yields, per day of the period, ``table name -> rows`` where each
row maps every column (server defaults included) to its value. It never reads
the current time: the period is fixed by ``end_date`` (inclusive) and ``days``.

The loader copies the users one by one into the ``DB_*`` database, one
transaction per user, and skips users whose seed device already exists.
Usage: docs/load-test-seeding.md.
"""

import argparse
import asyncio
import bisect
import json
import random
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

import asyncpg
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from mosemo.labels.domain.labels import DEFAULT_LABEL_NAMES
from mosemo.observation.domain.activities import ActivityRecord
from mosemo.observation.domain.timeline import (
    ProjectedActivity,
    ProjectedSegment,
    TimelineProjection,
    segment_version,
)
from mosemo.observation.presentation.activities.mappers import activity_batch
from mosemo.observation.presentation.activities.schemas import ActivityBatchRequest
from mosemo.platform.database.config import DatabaseSettings
from mosemo.shared.domain.timezones import Timezone

type Row = dict[str, object]
type Tables = dict[str, list[Row]]

DEVICE_NAMESPACE = UUID("6f2a0f0e-8a43-4f55-9d0b-5c1d3a4e7b21")
SEOUL = ZoneInfo(Timezone.ASIA_SEOUL.value)
ARCHIVED_LABEL_NAME = "보관된 라벨"
TABLE_ORDER = (
    "accounts",
    "devices",
    "labels",
    "focus_sessions",
    "activity_records",
    "activity_batch_receipts",
    "activity_timeline_segments",
    "activity_label_proposals",
    "activity_label_confirmations",
)

SAME_CONTEXT_INTERVAL = timedelta(seconds=30)
CONTEXT_CHANGE_PROBABILITY = 0.2
CHANGED_CONTEXT_MIN_MS = 5_000
CHANGED_CONTEXT_MAX_MS = 30_000
DETAILED_PROBABILITY = 0.8
VARIANT_PROBABILITY = 0.05
BATCH_WINDOW = timedelta(seconds=10)
ACTIVE_PERIODS = (
    (time(9), time(12), "screen_locked"),
    (time(13), time(21), "system_sleep"),
)
FOCUS_SESSIONS = ((time(10), 3000, 3000), (time(14), 3000, 3000), (time(16), 0, 2400))
FOCUS_SESSION_DURATION = timedelta(seconds=3000)
CONFIRMATION_PROBABILITY = 0.6
UNCLASSIFIED_PROBABILITY = 0.1
PROCESSING_PROBABILITY = 0.02
SUPERSEDED_PROBABILITY = 0.05
PROPOSAL_DELAY = timedelta(seconds=30)
CONFIRMATION_DELAY = timedelta(minutes=10)
LEASE_DURATION = timedelta(minutes=2)
MAX_RETRIEVED_EXAMPLES = 5

APPS: tuple[tuple[str, str, bool], ...] = (
    ("com.google.Chrome", "Google Chrome", True),
    ("com.apple.Safari", "Safari", True),
    ("org.mozilla.firefox", "Firefox", True),
    ("company.thebrowser.Browser", "Arc", True),
    ("com.microsoft.VSCode", "Visual Studio Code", False),
    ("com.jetbrains.pycharm", "PyCharm", False),
    ("com.apple.Terminal", "Terminal", False),
    ("com.googlecode.iterm2", "iTerm2", False),
    ("com.tinyspeck.slackmacgap", "Slack", False),
    ("com.kakao.KakaoTalkMac", "KakaoTalk", False),
    ("us.zoom.xos", "zoom.us", False),
    ("com.apple.mail", "Mail", False),
    ("com.apple.iCal", "Calendar", False),
    ("notion.id", "Notion", False),
    ("com.figma.Desktop", "Figma", False),
    ("com.apple.finder", "Finder", False),
    ("com.apple.Preview", "Preview", False),
    ("com.microsoft.Excel", "Microsoft Excel", False),
    ("com.spotify.client", "Spotify", False),
    ("com.apple.Notes", "Notes", False),
)
WINDOW_TITLES = (
    "seed_load.py — mosemo",
    "README.md — 프로젝트 문서",
    "zsh — 80×24",
    "#mosemo-dev 채널",
    "주간 회의록",
    "디자인 시안 v3",
    "2026 예산 계획.xlsx",
    "받은 편지함 (12)",
    "오늘의 할 일",
)
TABS = (
    ("GitHub - jolvy/mosemo", "https://github.com/jolvy/mosemo"),
    ("Python 3.14 문서", "https://docs.python.org/3.14/"),
    ("FastAPI", "https://fastapi.tiangolo.com/"),
    ("YouTube", "https://www.youtube.com/"),
    ("네이버 쇼핑", "https://shopping.naver.com/"),
    ("Stack Overflow - 질문", "https://stackoverflow.com/questions"),
    ("PostgreSQL: Documentation", "https://www.postgresql.org/docs/"),
    ("위키백과", "https://ko.wikipedia.org/"),
)
VARIANTS = (
    "window_absent",
    "window_unavailable",
    "tab_redacted",
    "url_redacted",
    "truncated",
)
BROWSER_ONLY_VARIANTS = frozenset({"tab_redacted", "url_redacted"})


@dataclass(frozen=True, slots=True)
class SeedPeriod:
    seed: int
    end_date: date
    days: int

    @property
    def first_date(self) -> date:
        return self.end_date - timedelta(days=self.days - 1)

    @property
    def start(self) -> datetime:
        return datetime.combine(self.first_date, time.min, tzinfo=UTC)


def seeded_uuid7(rng: random.Random, at: datetime) -> UUID:
    """A uuid7 carrying ``at`` in Unix milliseconds and random remaining bits."""
    milliseconds = (at - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(milliseconds=1)
    value = (
        (milliseconds << 80)
        | (0x7 << 76)
        | (rng.getrandbits(12) << 64)
        | (0b10 << 62)
        | rng.getrandbits(62)
    )
    return UUID(int=value)


def seeded_uuid4(rng: random.Random) -> UUID:
    return UUID(int=rng.getrandbits(128), version=4)


def generate_dummy_user(period: SeedPeriod, index: int) -> Iterator[Tables]:
    """Days of dummy user ``index`` (1-based); the first day adds the account and labels."""
    user_key = f"dummy:{index}"
    rng = _user_random(period, user_key)
    account_id = seeded_uuid7(rng, period.start)
    account = {
        "id": account_id,
        "provider": "KAKAO",
        "provider_subject": f"load-seed-{period.seed}-{index:06d}",
        "timezone": Timezone.ASIA_SEOUL.value,
        "created_at": period.start,
        "last_authenticated_at": period.start,
    }
    labels = [
        _label_row(
            rng, account_id=account_id, name=name, at=period.start, archived=False
        )
        for name in DEFAULT_LABEL_NAMES
    ]
    labels.append(
        _label_row(
            rng,
            account_id=account_id,
            name=ARCHIVED_LABEL_NAME,
            at=period.start,
            archived=True,
        )
    )
    active_label_ids = [
        cast(UUID, row["id"]) for row in labels if row["archived_at"] is None
    ]
    user = _UserGenerator(
        rng=rng,
        period=period,
        account_id=account_id,
        device_key=user_key,
        label_ids=active_label_ids,
    )
    return user.days({"accounts": [account], "labels": labels})


def generate_account_user(
    period: SeedPeriod, *, account_id: UUID, label_ids: Sequence[UUID]
) -> Iterator[Tables]:
    """Days for an existing account using its active ``label_ids``; no account or labels."""
    user_key = f"account:{account_id}"
    user = _UserGenerator(
        rng=_user_random(period, user_key),
        period=period,
        account_id=account_id,
        device_key=user_key,
        label_ids=list(label_ids),
    )
    return user.days({})


def device_idempotency_key(seed: int, user_key: str) -> UUID:
    """The seed device key of ``user_key``; its presence marks the user as loaded."""
    return uuid5(DEVICE_NAMESPACE, f"{seed}:{user_key}")


def _user_random(period: SeedPeriod, user_key: str) -> random.Random:
    return random.Random(f"{period.seed}:{user_key}")


def _label_row(
    rng: random.Random, *, account_id: UUID, name: str, at: datetime, archived: bool
) -> Row:
    return {
        "id": seeded_uuid7(rng, at),
        "account_id": account_id,
        "display_name": name,
        "created_at": at,
        "updated_at": at,
        "archived_at": at if archived else None,
    }


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class _Day:
    """Rows and pending requests of one generated day."""

    tables: Tables
    requests: list[dict[str, object]] = field(default_factory=list)
    focus_windows: list[tuple[datetime, datetime | None, UUID]] = field(
        default_factory=list
    )


class _UserGenerator:
    """One user's state carried across days: sequence, projection and history."""

    def __init__(
        self,
        *,
        rng: random.Random,
        period: SeedPeriod,
        account_id: UUID,
        device_key: str,
        label_ids: list[UUID],
    ) -> None:
        self._rng = rng
        self._period = period
        self._account_id = account_id
        self._label_ids = label_ids
        self._device_id = seeded_uuid7(rng, period.start)
        self._device_key = device_idempotency_key(period.seed, device_key)
        self._sequence = 0
        self._open_segment: ProjectedSegment | None = None
        self._confirmations: list[tuple[datetime, str]] = []

    def days(self, setup: Tables) -> Iterator[Tables]:
        device: Row = {
            "id": self._device_id,
            "account_id": self._account_id,
            "idempotency_key": self._device_key,
        }
        for offset in range(self._period.days):
            day = self._period.first_date + timedelta(days=offset)
            tables: Tables = {name: [] for name in TABLE_ORDER}
            if offset == 0:
                tables["devices"].append(device)
                for name, rows in setup.items():
                    tables[name].extend(rows)
            yield self._generate_day(day, tables, last=offset == self._period.days - 1)

    def _generate_day(self, day: date, tables: Tables, *, last: bool) -> Tables:
        generated = _Day(tables)
        self._add_focus_sessions(generated, day, last=last)
        for start, end, suspend_reason in ACTIVE_PERIODS:
            started_at = _seoul(day, start)
            ended_at = _seoul(day, end)
            self._add_state(generated, started_at, state="active", reason="user_active")
            final = last and suspend_reason == "system_sleep"
            self._add_observations(
                generated, started_at=started_at, ended_at=ended_at, final=final
            )
            if not final:
                self._add_state(
                    generated, ended_at, state="suspended", reason=suspend_reason
                )
        records = self._add_batches(generated)
        self._add_segments(generated, records, last=last)
        return {name: rows for name, rows in tables.items() if rows}

    def _add_focus_sessions(self, generated: _Day, day: date, *, last: bool) -> None:
        for start, target_seconds, work_seconds in FOCUS_SESSIONS:
            started_at = _seoul(day, start)
            in_progress = last and start == FOCUS_SESSIONS[-1][0]
            ended_at = None if in_progress else started_at + FOCUS_SESSION_DURATION
            session_id = seeded_uuid7(self._rng, started_at)
            generated.tables["focus_sessions"].append(
                {
                    "id": session_id,
                    "account_id": self._account_id,
                    "device_id": self._device_id,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "target_seconds": target_seconds,
                    "work_seconds": None if in_progress else work_seconds,
                    "label_id": None
                    if in_progress
                    else self._rng.choice(self._label_ids),
                    "description": "",
                }
            )
            generated.focus_windows.append((started_at, ended_at, session_id))

    def _add_observations(
        self, generated: _Day, *, started_at: datetime, ended_at: datetime, final: bool
    ) -> None:
        observed_at = started_at
        context = self._context()
        while observed_at < ended_at:
            self._add_observation(generated, observed_at=observed_at, context=context)
            if self._rng.random() < CONTEXT_CHANGE_PROBABILITY:
                context = self._context()
                observed_at += timedelta(
                    milliseconds=self._rng.randint(
                        CHANGED_CONTEXT_MIN_MS, CHANGED_CONTEXT_MAX_MS
                    )
                )
            else:
                observed_at += SAME_CONTEXT_INTERVAL
        if final:
            # The period ends observing: the last activity segment stays open.
            self._add_observation(generated, observed_at=ended_at, context=context)

    def _add_observation(
        self, generated: _Day, *, observed_at: datetime, context: dict[str, object]
    ) -> None:
        record = self._record_base(observed_at)
        record["focusSessionId"] = _focus_session(generated, observed_at)
        record["recordType"] = "activity_observation"
        record["context"] = context
        generated.requests.append(record)

    def _add_state(
        self, generated: _Day, observed_at: datetime, *, state: str, reason: str
    ) -> None:
        record = self._record_base(observed_at)
        record["recordType"] = "collection_state_changed"
        record["state"] = state
        record["reason"] = reason
        generated.requests.append(record)

    def _record_base(self, observed_at: datetime) -> dict[str, object]:
        offset = observed_at.astimezone(SEOUL).utcoffset()
        assert offset is not None
        record: dict[str, object] = {
            "eventId": str(seeded_uuid7(self._rng, observed_at)),
            "sequence": self._sequence,
            "observedAt": _timestamp(observed_at),
            "timezoneId": Timezone.ASIA_SEOUL.value,
            "utcOffsetMinutes": offset // timedelta(minutes=1),
        }
        self._sequence += 1
        return record

    def _context(self) -> dict[str, object]:
        if self._rng.random() >= DETAILED_PROBABILITY:
            return {"kind": "opaque"}
        bundle_id, name, browser = self._rng.choice(APPS)
        if browser:
            title, url = self._rng.choice(TABS)
            web: dict[str, object] = {
                "kind": "browser",
                "tabTitle": {"status": "captured", "value": title},
                "url": {"status": "captured", "value": url},
            }
        else:
            title = self._rng.choice(WINDOW_TITLES)
            web = {"kind": "not_applicable"}
        context: dict[str, object] = {
            "kind": "detailed",
            "app": {
                "bundleId": {"status": "captured", "value": bundle_id},
                "name": {"status": "captured", "value": name},
            },
            "window": {
                "status": "captured",
                "title": {"status": "captured", "value": title},
            },
            "web": web,
        }
        if self._rng.random() < VARIANT_PROBABILITY:
            variants = (
                VARIANTS
                if browser
                else [
                    variant
                    for variant in VARIANTS
                    if variant not in BROWSER_ONLY_VARIANTS
                ]
            )
            _apply_variant(context, variant=self._rng.choice(variants), title=title)
        return context

    def _add_batches(self, generated: _Day) -> list[ActivityRecord]:
        records: list[ActivityRecord] = []
        for window_end, request_records in _batch_windows(generated.requests):
            request = ActivityBatchRequest.model_validate(
                {
                    "batchId": str(seeded_uuid7(self._rng, window_end)),
                    "deviceId": str(self._device_id),
                    "records": request_records,
                }
            )
            batch = activity_batch(request)
            generated.tables["activity_batch_receipts"].append(
                {
                    "account_id": self._account_id,
                    "device_id": self._device_id,
                    "batch_id": batch.batch_id,
                    "fingerprint": batch.fingerprint,
                    "results": [
                        {"eventId": str(record.event_id), "status": "stored"}
                        for record in batch.records
                    ],
                    "processed_at": window_end,
                }
            )
            for record in batch.records:
                generated.tables["activity_records"].append(
                    _record_row(record, received_at=window_end)
                )
            records.extend(batch.records)
        return records

    def _add_segments(
        self, generated: _Day, records: list[ActivityRecord], *, last: bool
    ) -> None:
        projection = TimelineProjection(self._open_segment)
        for record in records:
            projection.feed(record)
        segments = projection.segments
        if not last:
            self._open_segment = segments[-1]
            segments = segments[:-1]
        for projected in segments:
            segment = projected.to_segment(
                account_id=self._account_id,
                segment_id=seeded_uuid7(self._rng, projected.started_at),
            )
            generated.tables["activity_timeline_segments"].append(
                {
                    "id": segment.id,
                    "account_id": segment.account_id,
                    "segment_type": segment.segment_type.value,
                    "started_at": segment.started_at,
                    "ended_at": segment.ended_at,
                    "first_event_id": segment.first_event_id,
                    "last_event_id": segment.last_event_id,
                    "last_observed_at": segment.last_observed_at,
                    "context": segment.context,
                    "reason": segment.reason,
                }
            )
            if (
                isinstance(projected, ProjectedActivity)
                and projected.ended_at is not None
                and projected.context.get("kind") == "detailed"
            ):
                self._add_labeling(generated, projected)

    def _add_labeling(self, generated: _Day, projected: ProjectedActivity) -> None:
        assert projected.ended_at is not None
        segment = projected.to_segment(account_id=self._account_id, segment_id=None)
        version = segment_version(segment, ended_at=projected.ended_at)
        if self._rng.random() < CONFIRMATION_PROBABILITY:
            confirmed_at = projected.ended_at + CONFIRMATION_DELAY
            confirmation_id = seeded_uuid7(self._rng, confirmed_at)
            generated.tables["activity_label_confirmations"].append(
                {
                    "id": confirmation_id,
                    "account_id": self._account_id,
                    "first_event_id": projected.first_event_id,
                    "segment_version": version,
                    "label_id": self._optional_label(),
                    "confirmed_at": confirmed_at,
                    "updated_at": confirmed_at,
                }
            )
            self._confirmations.append((confirmed_at, str(confirmation_id)))
        created_at = projected.ended_at + PROPOSAL_DELAY
        proposal = self._proposal_base(
            projected, version=version, created_at=created_at
        )
        if self._rng.random() < PROCESSING_PROBABILITY:
            proposal["status"] = "processing"
            proposal["lease_token"] = seeded_uuid4(self._rng)
            proposal["lease_expires_at"] = created_at + LEASE_DURATION
        else:
            proposal.update(self._ready_fields(created_at))
        generated.tables["activity_label_proposals"].append(proposal)
        if self._rng.random() < SUPERSEDED_PROBABILITY:
            previous_ended_at = projected.ended_at - PROPOSAL_DELAY
            superseded = self._proposal_base(
                projected,
                version=segment_version(segment, ended_at=previous_ended_at),
                created_at=previous_ended_at + PROPOSAL_DELAY,
            )
            superseded["status"] = "superseded"
            generated.tables["activity_label_proposals"].append(superseded)

    def _proposal_base(
        self, projected: ProjectedActivity, *, version: str, created_at: datetime
    ) -> Row:
        return {
            "id": seeded_uuid7(self._rng, created_at),
            "account_id": self._account_id,
            "first_event_id": projected.first_event_id,
            "segment_version": version,
            "status": None,
            "suggested_label_id": None,
            "attempt_count": 1,
            "lease_token": None,
            "lease_expires_at": None,
            "next_attempt_at": None,
            "provider": None,
            "model": None,
            "prompt_version": None,
            "retrieved_example_ids": None,
            "latency_ms": None,
            "input_tokens": None,
            "output_tokens": None,
            "suggested_at": None,
            "created_at": created_at,
            "updated_at": created_at,
        }

    def _ready_fields(self, suggested_at: datetime) -> Row:
        before = bisect.bisect_left(self._confirmations, (suggested_at, ""))
        examples = self._confirmations[max(0, before - MAX_RETRIEVED_EXAMPLES) : before]
        return {
            "status": "ready",
            "suggested_label_id": self._optional_label(),
            "provider": "seed",
            "model": "seed",
            "prompt_version": "seed",
            "retrieved_example_ids": [example_id for _, example_id in examples],
            "latency_ms": self._rng.randint(800, 3000),
            "input_tokens": self._rng.randint(1500, 3000),
            "output_tokens": self._rng.randint(20, 60),
            "suggested_at": suggested_at,
        }

    def _optional_label(self) -> UUID | None:
        if self._rng.random() < UNCLASSIFIED_PROBABILITY:
            return None
        return self._rng.choice(self._label_ids)


def _seoul(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=SEOUL).astimezone(UTC)


def _focus_session(generated: _Day, observed_at: datetime) -> str | None:
    for started_at, ended_at, session_id in generated.focus_windows:
        if started_at <= observed_at and (ended_at is None or observed_at <= ended_at):
            return str(session_id)
    return None


def _apply_variant(context: dict[str, object], *, variant: str, title: str) -> None:
    web = cast(dict[str, object], context["web"])
    if variant == "window_absent":
        context["window"] = {"status": "absent"}
    elif variant == "window_unavailable":
        context["window"] = {"status": "unavailable", "reason": "accessibility_denied"}
    elif variant == "tab_redacted":
        web["tabTitle"] = {"status": "redacted", "reason": "private_browsing"}
    elif variant == "url_redacted":
        web["url"] = {"status": "redacted", "reason": "sensitive_domain"}
    else:
        truncated = {
            "status": "captured",
            "value": title[: len(title) // 2],
            "truncated": True,
            "originalByteLength": len(title.encode("utf-8")),
        }
        context["window"] = {"status": "captured", "title": truncated}
        if web["kind"] == "browser":
            web["tabTitle"] = dict(truncated)


def _batch_windows(
    requests: list[dict[str, object]],
) -> Iterator[tuple[datetime, list[dict[str, object]]]]:
    window_end: datetime | None = None
    batch: list[dict[str, object]] = []
    for request in requests:
        observed_at = datetime.fromisoformat(cast(str, request["observedAt"]))
        end = _window_end(observed_at)
        if batch and end != window_end:
            assert window_end is not None
            yield window_end, batch
            batch = []
        window_end = end
        batch.append(request)
    if batch:
        assert window_end is not None
        yield window_end, batch


def _window_end(observed_at: datetime) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return epoch + ((observed_at - epoch) // BATCH_WINDOW + 1) * BATCH_WINDOW


def _record_row(record: ActivityRecord, *, received_at: datetime) -> Row:
    return {
        "event_id": record.event_id,
        "device_id": record.device_id,
        "focus_session_id": record.focus_session_id,
        "sequence": record.sequence,
        "record_type": record.record_type.value,
        "observed_at": record.observed_at,
        "timezone_id": record.timezone_id.value,
        "utc_offset_minutes": record.utc_offset_minutes,
        "payload": record.payload,
        "received_at": received_at,
    }


# Loader -----------------------------------------------------------------------

EXIT_FAILED = 1
EXIT_USAGE = 2
# Database and connection failures; other errors propagate and also exit with 1.
LOAD_ERRORS = (SQLAlchemyError, asyncpg.PostgresError, asyncpg.InterfaceError, OSError)
JSON_COLUMNS = frozenset({"payload", "context", "results", "retrieved_example_ids"})


class RejectedAccountError(Exception):
    """The ``--account-id`` account cannot take seed data; nothing is written."""


@dataclass(frozen=True, slots=True)
class LoadResult:
    loaded: int
    skipped: int


async def load_users(
    connection: AsyncConnection,
    period: SeedPeriod,
    *,
    users: int,
    account_id: UUID | None,
) -> LoadResult:
    """Load the existing account first (if any), then dummies up to ``users``."""
    outcomes: list[bool] = []
    if account_id is not None:
        outcomes.append(
            await _load_account_user(connection, period, account_id=account_id)
        )
    dummies = users - len(outcomes)
    for index in range(1, dummies + 1):
        outcomes.append(await _load_dummy_user(connection, period, index=index))
    loaded = sum(outcomes)
    return LoadResult(loaded=loaded, skipped=len(outcomes) - loaded)


async def _load_dummy_user(
    connection: AsyncConnection, period: SeedPeriod, *, index: int
) -> bool:
    user_key = f"dummy:{index}"
    async with connection.begin():
        # The SQLAlchemy asyncpg adapter sends BEGIN with the first statement, so
        # this lookup opens the transaction the COPY below runs in.
        if await _device_exists(connection, period, user_key=user_key):
            return _report_user(user_key, loaded=False)
        await copy_days(connection, generate_dummy_user(period, index))
    return _report_user(user_key, loaded=True)


async def _load_account_user(
    connection: AsyncConnection, period: SeedPeriod, *, account_id: UUID
) -> bool:
    user_key = f"account:{account_id}"
    async with connection.begin():
        exists = await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM accounts WHERE id = :id)"),
            {"id": account_id},
        )
        if not exists:
            raise RejectedAccountError(f"account {account_id} does not exist")
        if await _device_exists(connection, period, user_key=user_key):
            return _report_user(user_key, loaded=False)
        label_ids = await _account_label_ids(connection, account_id)
        days = generate_account_user(period, account_id=account_id, label_ids=label_ids)
        await copy_days(connection, days)
    return _report_user(user_key, loaded=True)


async def _device_exists(
    connection: AsyncConnection, period: SeedPeriod, *, user_key: str
) -> bool:
    return bool(
        await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM devices WHERE idempotency_key = :key)"),
            {"key": device_idempotency_key(period.seed, user_key)},
        )
    )


async def _account_label_ids(
    connection: AsyncConnection, account_id: UUID
) -> list[UUID]:
    """Active label IDs of an account without activity data, else rejected.

    The service projects all devices of an account together, so seed records
    mixed with existing ones would break the timeline and label versions.
    """
    has_activity = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM activity_records r"
            " JOIN devices d ON d.id = r.device_id WHERE d.account_id = :id)"
            " OR EXISTS (SELECT 1 FROM activity_timeline_segments"
            " WHERE account_id = :id)"
        ),
        {"id": account_id},
    )
    if has_activity:
        raise RejectedAccountError(f"account {account_id} already has activity data")
    result = await connection.scalars(
        text(
            "SELECT id FROM labels WHERE account_id = :id AND archived_at IS NULL"
            " ORDER BY id"
        ),
        {"id": account_id},
    )
    label_ids = list(result)
    if not label_ids:
        raise RejectedAccountError(f"account {account_id} has no active label")
    return label_ids


def _report_user(user_key: str, *, loaded: bool) -> bool:
    print(f"{'loaded' if loaded else 'skipped'} {user_key}", flush=True)
    return loaded


async def copy_days(connection: AsyncConnection, days: Iterable[Tables]) -> None:
    """COPY each day's rows in foreign key order on the connection's transaction."""
    raw = await connection.get_raw_connection()
    driver = cast(asyncpg.Connection, raw.driver_connection)
    for tables in days:
        for table in TABLE_ORDER:
            if rows := tables.get(table):
                await copy_rows(driver, table=table, rows=rows)


async def copy_rows(driver: asyncpg.Connection, *, table: str, rows: list[Row]) -> None:
    columns = list(rows[0])
    await driver.copy_records_to_table(
        table,
        columns=columns,
        records=[
            tuple(_copy_value(column, row[column]) for column in columns)
            for row in rows
        ],
    )


def _copy_value(column: str, value: object) -> object:
    # SQLAlchemy registers a JSONB codec that takes JSON text.
    if column in JSON_COLUMNS and value is not None:
        return json.dumps(value, ensure_ascii=False)
    return value


async def measure_sizes(connection: AsyncConnection) -> list[tuple[str, int]]:
    """``ANALYZE`` the seeded tables, then their total sizes and the database size."""
    async with connection.begin():
        await connection.execute(text(f"ANALYZE {', '.join(TABLE_ORDER)}"))
        sizes = [
            (
                table,
                cast(
                    int,
                    await connection.scalar(
                        text("SELECT pg_total_relation_size(CAST(:name AS regclass))"),
                        {"name": table},
                    ),
                ),
            )
            for table in TABLE_ORDER
        ]
        database = await connection.scalar(
            text("SELECT pg_database_size(current_database())")
        )
    return [*sizes, ("(database)", cast(int, database))]


def print_report(result: LoadResult, sizes: Sequence[tuple[str, int]]) -> None:
    print(f"{'loaded users':<30} {result.loaded:>16}")
    print(f"{'skipped users':<30} {result.skipped:>16}")
    print(f"{'table':<30} {'total bytes':>16}")
    for name, size in sizes:
        print(f"{name:<30} {size:>16}")


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more: {value}")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load deterministic load-test seed data into the DB_* database."
    )
    parser.add_argument("--users", type=_positive_int, required=True)
    parser.add_argument(
        "--confirm-host", required=True, help="must equal DB_HOST exactly"
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        required=True,
        help="last day of the period (ISO date)",
    )
    parser.add_argument("--account-id", type=UUID)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--days", type=_positive_int, default=365)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; argument errors exit with 2 before connecting."""
    args = _parser().parse_args(argv)
    config = DatabaseSettings().database
    if args.confirm_host != config.host:
        print(
            f"--confirm-host {args.confirm_host!r} does not match DB_HOST",
            file=sys.stderr,
        )
        return EXIT_USAGE
    period = SeedPeriod(seed=args.seed, end_date=args.end_date, days=args.days)
    return asyncio.run(
        _run(config.dsn, period, users=args.users, account_id=args.account_id)
    )


async def _run(
    dsn: str, period: SeedPeriod, *, users: int, account_id: UUID | None
) -> int:
    engine = create_async_engine(dsn)
    try:
        async with engine.connect() as connection:
            try:
                result = await load_users(
                    connection, period, users=users, account_id=account_id
                )
            except RejectedAccountError as exc:
                print(f"rejected: {exc}", file=sys.stderr)
                return EXIT_USAGE
            except LOAD_ERRORS as exc:
                print(
                    f"stopped; the failing user was rolled back: {exc!r}",
                    file=sys.stderr,
                )
                return EXIT_FAILED
            sizes = await measure_sizes(connection)
    finally:
        await engine.dispose()
    print_report(result, sizes)
    return 0


if __name__ == "__main__":
    sys.exit(main())

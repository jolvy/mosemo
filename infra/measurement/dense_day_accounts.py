"""측정 B: 오늘 활동 구간이 정확히 N개인 계정을 DB에 바로 넣는다(업로드 API를 쓰지 않는다).

계정마다 기기 1개, 오늘(계정 시간대) 이전 기록은 없다. 구간 N개의 시작 관찰은 앞 구간과 다른 context를 갖고
오늘 00:00부터 생성 시점 90초 전 사이에 고르게 놓는다. 구간 시작 사이가 FILL_INTERVAL보다 길면 같은 context의 관찰을
FILL_INTERVAL마다 채워 이웃 관찰 간격이 MAX_OBSERVATION_GAP(60초)을 넘지 않게 한다(구간 수는 그대로, 구간 길이는 실제 모양). 구간은 앱과 같은 `TimelineProjection`으로
만들고 `finish(now)`로 마지막 구간을 마지막 관찰 시각에 닫는다. 그래서 조회 로그의 `segment_count`가
SQL로 센 오늘 구간 수와 같다.

  dense_day_accounts.py --counts 1000,5000,10000 --out-dir <results 폴더>
  결과: b-dense.txt(`dense <구간 수> ...`, --counts 순서), b-dense.json({"<N>": 계정 ID}),
        b-dense-date.txt(계정을 만든 날짜, 계정 시간대)
  DB 접속은 하네스(db_harness.py)와 같다(--dsn 또는 DB_*·S5_DSN).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from mosemo.observation.domain.activities import MAX_OBSERVATION_GAP
from mosemo.observation.domain.timeline import TimelineProjection
from mosemo.observation.presentation.activities.mappers import activity_batch
from mosemo.observation.presentation.activities.schemas import ActivityBatchRequest
from mosemo.platform.clock import utc_now

HERE = Path(__file__).resolve().parent
BATCH_RECORDS = 200  # 업로드 묶음 하나의 최대 기록 수(ActivityBatchRequest)
LAST_OBSERVATION_LEAD = timedelta(
    seconds=90
)  # MAX_OBSERVATION_GAP(60초)보다 길어 마지막 구간이 닫힌다
MIN_CONTEXTS = 2
FILL_INTERVAL = timedelta(seconds=55)  # MAX_OBSERVATION_GAP보다 짧다
Q_CONTEXTS = (
    "SELECT payload FROM activity_records WHERE record_type = 'activity_observation' "
    "LIMIT 200"
)
Q_TODAY_SEGMENTS = """
SELECT count(*) FROM activity_timeline_segments
WHERE account_id = $1 AND segment_type = 'activity' AND started_at >= $2 AND started_at < $3"""


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("db_harness", HERE / "db_harness.py")
    if spec is None or spec.loader is None:
        raise ImportError(HERE / "db_harness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _load_harness()


def day_bounds(today: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(today, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


def distinct_contexts(payloads: list[Any]) -> list[dict]:
    seen: dict[str, dict] = {}
    for p in payloads:
        ctx = p.get("context") if isinstance(p, dict) else None
        if isinstance(ctx, dict):
            seen.setdefault(json.dumps(ctx, sort_keys=True), ctx)
    return list(seen.values())


def utc_text(at: datetime) -> str:
    """업로드 계약 형식: RFC 3339 UTC, 밀리초, Z로 끝남(k6 toISOString과 같다)."""
    return at.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def observation_plan(
    count: int, window: tuple[datetime, datetime]
) -> list[tuple[datetime, int]]:
    """(관찰 시각, 구간 번호). 구간 i는 window 안 고른 시각에 시작하고, 다음 구간 시작 전까지
    FILL_INTERVAL마다 같은 구간의 관찰을 더한다."""
    start, end = window
    step = (end - start) / count
    if step < timedelta(milliseconds=1):
        sys.exit(f"오늘 남은 시간이 짧아 구간 {count}개를 1ms 간격으로도 놓을 수 없다")
    plan = []
    for i in range(count):
        at, nxt = start + step * i, start + step * (i + 1)
        while at < nxt and (not plan or at > plan[-1][0]):
            plan.append((at, i))
            at += FILL_INTERVAL
    return plan


def max_gap(plan: list[tuple[datetime, int]]) -> timedelta:
    return max(
        (b[0] - a[0] for a, b in pairwise(plan)),
        default=timedelta(),
    )


def build_records(
    *,
    plan: list[tuple[datetime, int]],
    device_id: uuid.UUID,
    contexts: list[dict],
    zone_name: str,
) -> list:
    """관찰 계획을 기록으로. 구간 i의 관찰은 contexts[i % len]이라 이웃한 구간의 context가 늘 다르다."""
    offset = int((plan[0][0].utcoffset() or timedelta()).total_seconds() // 60)
    records = []
    for first in range(0, len(plan), BATCH_RECORDS):
        chunk = [
            {
                "eventId": str(uuid.uuid4()),
                "sequence": n + 1,
                "observedAt": utc_text(at),
                "timezoneId": zone_name,
                "utcOffsetMinutes": offset,
                "recordType": "activity_observation",
                "context": contexts[i % len(contexts)],
            }
            for n, (at, i) in enumerate(
                plan[first : first + BATCH_RECORDS], start=first
            )
        ]
        request = ActivityBatchRequest.model_validate(
            {"batchId": str(uuid.uuid4()), "deviceId": str(device_id), "records": chunk}
        )
        records.extend(activity_batch(request).records)
    return records


def record_row(record, *, received_at: datetime) -> tuple:
    return (
        record.event_id,
        record.device_id,
        record.focus_session_id,
        record.sequence,
        record.record_type.value,
        record.observed_at,
        record.timezone_id.value,
        record.utc_offset_minutes,
        record.payload,
        received_at,
    )


def segment_rows(records: list, *, account_id: uuid.UUID, now: datetime) -> list:
    projection = TimelineProjection()
    for record in records:
        projection.feed(record)
    rows = []
    for projected in projection.finish(now):
        s = projected.to_segment(account_id=account_id, segment_id=uuid.uuid4())
        rows.append(
            (
                s.id,
                s.account_id,
                s.segment_type.value,
                s.started_at,
                s.ended_at,
                s.first_event_id,
                s.last_event_id,
                s.last_observed_at,
                s.context,
                s.reason,
            )
        )
    return rows


async def insert_account(
    conn, *, count: int, contexts: list[dict], day: date, zone_name: str
) -> uuid.UUID:
    """`day`(계정 시간대 날짜) 00:00부터 지금 90초 전 사이에 구간 `count`개."""
    now = utc_now()
    start, _ = day_bounds(day, ZoneInfo(zone_name))
    end = now - LAST_OBSERVATION_LEAD
    if end <= start:
        sys.exit("오늘이 시작된 지 90초가 지나지 않았다")
    account_id, device_id = uuid.uuid4(), uuid.uuid4()
    plan = observation_plan(count, (start, end))
    if max_gap(plan) > MAX_OBSERVATION_GAP:
        sys.exit(f"이웃 관찰 간격이 {MAX_OBSERVATION_GAP}를 넘는다: {max_gap(plan)}")
    records = build_records(
        plan=plan, device_id=device_id, contexts=contexts, zone_name=zone_name
    )
    segments = segment_rows(records, account_id=account_id, now=now)
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO accounts (id, provider, provider_subject, timezone, created_at,"
            " last_authenticated_at) VALUES ($1, 'KAKAO', $2, $3, $4, $4)",
            account_id,
            f"measure-b-dense-{count}-{account_id}",
            zone_name,
            start,
        )
        await conn.execute(
            "INSERT INTO devices (id, account_id, idempotency_key) VALUES ($1, $2, $3)",
            device_id,
            account_id,
            uuid.uuid4(),
        )
        await conn.executemany(
            "INSERT INTO activity_records (event_id, device_id, focus_session_id, sequence,"
            " record_type, observed_at, timezone_id, utc_offset_minutes, payload, received_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            [record_row(r, received_at=now) for r in records],
        )
        await conn.executemany(
            "INSERT INTO activity_timeline_segments (id, account_id, segment_type, started_at,"
            " ended_at, first_event_id, last_event_id, last_observed_at, context, reason)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            segments,
        )
    return account_id


@dataclass(frozen=True)
class DenseResult:
    counts: list[int]  # 기대 구간 수(--counts 순서)
    ids: dict[str, uuid.UUID]
    got: list[int]  # SQL로 센 오늘 구간 수
    day: date  # 계정을 만든 날짜(계정 시간대)


async def run(a: argparse.Namespace) -> DenseResult:
    counts = [int(x) for x in a.counts.split(",")]
    zone = ZoneInfo(a.timezone)
    conn = await H.connect(a.dsn)
    try:
        contexts = distinct_contexts(
            [r["payload"] for r in await conn.fetch(Q_CONTEXTS)]
        )
        if len(contexts) < MIN_CONTEXTS:
            sys.exit("서로 다른 context가 2개 미만이다(시드 기록이 없다)")
        day = utc_now().astimezone(zone).date()  # 세 계정 모두 같은 날짜
        ids = {}
        for count in counts:
            ids[str(count)] = await insert_account(
                conn, count=count, contexts=contexts, day=day, zone_name=a.timezone
            )
        start, end = day_bounds(day, zone)
        got = [
            await conn.fetchval(Q_TODAY_SEGMENTS, ids[str(c)], start, end)
            for c in counts
        ]
    finally:
        await H.close_conn(conn)
    return DenseResult(counts, ids, got, day)


def write_results(out: Path, r: DenseResult) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "b-dense.json").write_text(
        json.dumps({k: str(v) for k, v in r.ids.items()}) + "\n"
    )
    (out / "b-dense.txt").write_text("dense " + " ".join(map(str, r.got)) + "\n")
    (out / "b-dense-date.txt").write_text(r.day.isoformat() + "\n")
    print(json.dumps({"counts": r.counts, "today_segments": r.got}))
    if r.got != r.counts:
        sys.exit(f"오늘 구간 수가 기대와 다르다: {r.got} != {r.counts}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dsn", default=H.build_parser().get_default("dsn"))
    p.add_argument("--counts", default="1000,5000,10000")
    p.add_argument("--timezone", default="Asia/Seoul")
    p.add_argument("--out-dir", required=True)
    a = p.parse_args()
    if not a.dsn:
        p.error("--dsn 또는 DB_*·S5_DSN이 필요하다")
    write_results(Path(a.out_dir), asyncio.run(run(a)))


if __name__ == "__main__":
    main()

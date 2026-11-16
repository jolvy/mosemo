"""API 한계·기준 사양 측정 — k6 입력 준비와 결과 분석. Pod 안에서 실행한다.

하네스(db_harness.py)의 DB 연결·시간 제한 함수만 import한다.
JWT는 Secret 환경 변수 키(AUTH_JWT_SECRET_KEY)로 여기서 만든다(k6에서 서명하지 않음).

  plan                       1천 DAU 기준 예상 업로드 rps(measured 프로필)
  workload --total 100       고정 부하 workload-<N>rps.md·.json(조회:업로드 비율, 판정 기준). 7단계 뒤 비교에도 같은 파일
  tokens                     조회용 tokens.json(계정 토큰 + 지난 날짜, today)
  payloads                   업로드 계단용 payloads-x{배수}.json(+ .rows.json), devices.json, contexts.json
  payloads-mixed             mixed 반복별 payloads-mixed-r{k}.json(+ .rows.json)
  report-read  <stage>       조회 계단 단계 통계 → stages-read.jsonl, 멈춰야 하면 exit 3
  report-write <stage>       업로드 계단 단계 통계 + 처리 완료 지연·미처리 → stages-write.jsonl, 멈춰야 하면 exit 3
  summary <read|write>       계단 표와 "예산 안 최대 rps"·"오류 시작 rps"
  backlog-watch <stage>      실행 중 30초마다 미처리 표본(backlog-<stage>.jsonl). 기울기 계산용
  report-mixed <stage>       mixed 한 번의 지표와 workload 판정 기준별 통과·실패(mixed-<stage>.json·.md)
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import importlib.util
import json
import math
import os
import random
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from mosemo.platform.clock import utc_now

HERE = Path(__file__).resolve().parent


def _load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("db_harness", HERE / "db_harness.py")
    if spec is None or spec.loader is None:
        raise ImportError(HERE / "db_harness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _load_harness()

CHUNK = 500  # k6 묶음 덩어리 크기(행)
RECEIPTS_PER_USER_DAY = (
    2934  # measured 10명×7일 실측(STEP5-PLAN 1.2절): 명·일 배치 영수증
)
RECORDS_PER_BATCH = 5500 / 2934  # 1.87(명·일 레코드 5,500)
READS_PER_USER_DAY = (
    41  # PREREG 2.2절 중간(V = 30분, 사용자 결정): 열기 11 + 오늘 화면 새로고침 30
)
ACTIVE_WINDOW_S = 15 * 3600  # 08~23시
PEAK_HOUR = 2.5
READ_P95_BUDGET_MS = 300  # 조회 예산(사용자 결정)
READ_P99_BUDGET_MS = 500
ERROR_BUDGET = 0.01  # 계단 단계의 "예산 안" 오류율 상한
STOP_ERROR_RATE = 0.05  # 계단을 멈추는 오류율
STOP_P99_MS = 5000  # 계단을 멈추는 p99
MIN_OLS_POINTS = 3


def expected_upload(dau: int = 1000) -> dict:
    avg = dau * RECEIPTS_PER_USER_DAY / ACTIVE_WINDOW_S
    return {
        "dau": dau,
        "batches_per_user_day": RECEIPTS_PER_USER_DAY,
        "records_per_batch": round(RECORDS_PER_BATCH, 2),
        "avg_batches_per_s": round(avg, 1),
        "peak_hour_batches_per_s": round(avg * PEAK_HOUR, 1),
        "avg_records_per_s": round(avg * RECORDS_PER_BATCH, 1),
    }


def token_for(account_id: str) -> str:
    import jwt

    now = utc_now()
    return jwt.encode(
        {
            "sub": account_id,
            "iss": os.environ.get("AUTH_JWT_ISSUER", "mosemo"),
            "aud": os.environ.get("AUTH_JWT_AUDIENCE", "mosemo-api"),
            "iat": now,
            "exp": now + timedelta(hours=13),
            "jti": str(uuid.uuid4()),
            "token_type": "access",
        },
        os.environ["AUTH_JWT_SECRET_KEY"],
        algorithm="HS256",
    )


def env_dsn() -> str:
    from urllib.parse import quote

    if os.environ.get("S5_DSN"):
        return os.environ["S5_DSN"]
    return (
        f"postgresql://{quote(os.environ['DB_USER'])}:{quote(os.environ.get('DB_PASSWORD', ''))}"
        f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '5432')}/{os.environ['DB_DATABASE']}"
    )


def rank(xs, p):
    xs = sorted(xs)
    return xs[max(1, math.ceil(p * len(xs))) - 1] if xs else None


def r1(v):
    return None if v is None else round(v, 1)


def r3(v):
    return None if v is None or v == math.inf else round(v, 3)


# --------------------------------------------------------------------------- 고정 부하(workload)


def cmd_workload(a) -> None:
    per_user = READS_PER_USER_DAY + RECEIPTS_PER_USER_DAY
    read_share = READS_PER_USER_DAY / per_user
    total = a.total
    dau_equiv = total / (per_user / ACTIVE_WINDOW_S)
    w = {
        "total_rps": total,
        "read_share": read_share,
        "read_rps": total * read_share,
        "write_rps": total * (1 - read_share),
        "reads_per_user_day": READS_PER_USER_DAY,
        "uploads_per_user_day": RECEIPTS_PER_USER_DAY,
        "records_per_batch": round(RECORDS_PER_BATCH, 3),
        "active_window_s": ACTIVE_WINDOW_S,
        "dau_equivalent_avg": round(dau_equiv),
        "dau_equivalent_peak_hour": round(dau_equiv / PEAK_HOUR),
        "duration_s": a.duration,
        "runs": a.runs,
        "warm_s": a.warm,
        "grace_s": a.grace,
        "criteria": {
            "read_p95_ms": 300,
            "read_p99_ms": 500,
            "error_rate_max": 0.001,
            "reflect_p95_s": a.reflect_p95,
            "backlog_slope": "OLS 기울기 95% 신뢰구간 하한 ≤ 0",
            "dropped_iterations": 0,
            "arrival_tolerance": 0.02,
        },
    }
    name = f"workload-{total:g}rps"
    Path(a.out_dir, name + ".json").write_text(
        json.dumps(w, indent=1, ensure_ascii=False)
    )
    one_k = expected_upload(1000)
    md = f"""# 고정 부하 {total:g} rps(조회 + 업로드) — 기준 사양 측정·7단계 뒤 비교용

생성: `k6_inputs_and_reports.py workload --total {total:g}`(이 파일과 같은 이름의 `.json`을 k6 mixed가 읽는다). 사전 등록 형식이며, 측정 뒤 이 값을 바꾸지 않는다.
목적: 현재 코드 그대로 이 부하를 아래 기준 안에서 버티는 **기준 사양**을 정한다. 7단계 개발 뒤 같은 사양·같은 파일·같은 인자로 다시 재 비교한다. 최적화·프로파일링은 하지 않는다.

## 1. 구성(계산 근거)

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 조회 | 사용자당 하루 **{READS_PER_USER_DAY}건** | STEP5-T0-PREREG 2.2절 중간(V = 30분, 사용자 결정): 화면 열기 11 + 오늘 화면 60초 새로고침 30. `GET /api/v1/activities/timeline`, 날짜는 S1 구성(오늘 90%) |
| 업로드 | 사용자당 하루 **{RECEIPTS_PER_USER_DAY}묶음**(묶음당 {RECORDS_PER_BATCH:.2f}건) | measured 프로필 실측(STEP5-PLAN 1.2절, 10명×7일 배치 영수증). 시드는 같은 기기 레코드를 10초 창으로 묶는다(관찰 5.5초 간격 → 창마다 1~2건). `POST /api/v1/activities/batches` |
| 조회 비율 | {READS_PER_USER_DAY} ÷ ({READS_PER_USER_DAY} + {RECEIPTS_PER_USER_DAY}) = **{read_share * 100:.3f}%** | 두 요청이 같은 활동 창(08~23시)·같은 피크 계수(2.5)를 쓰므로 비율은 시각과 상관없다 |
| **{total:g} rps** | **조회 {w["read_rps"]:.2f} rps + 업로드 {w["write_rps"]:.2f} 묶음/초** | k6 mixed: constant-arrival-rate 두 시나리오, timeUnit 100s에 rate = 목표 × 100 |
| 1천 DAU와의 관계 | 평균 {one_k["avg_batches_per_s"] + 1000 * READS_PER_USER_DAY / ACTIVE_WINDOW_S:.1f} rps, 피크 시간 {(one_k["avg_batches_per_s"] + 1000 * READS_PER_USER_DAY / ACTIVE_WINDOW_S) * PEAK_HOUR:.1f} rps | {total:g} rps는 평균 기준 약 {w["dau_equivalent_avg"]:,} DAU, 피크 시간 기준 약 {w["dau_equivalent_peak_hour"]:,} DAU에 해당 |
| 한계 | 업로드 비율은 시드의 10초 창 묶음 가정에 기댄다 | 실제 macOS 클라이언트 전송 주기는 확인하지 않았다(STEP5-PLAN U-S5). 클라이언트가 더 길게 묶으면 업로드 rps가 줄고 조회 비율이 는다 |

## 2. 실행

- 총 **{total:g} rps × {a.duration // 60:g}분**, **{a.runs}회**. 앞 {a.warm:g}초는 웜업으로 빼고 판정한다. 각 회차 뒤 {a.grace:g}초 기다렸다가 반영 여부를 본다.
- consumer replicas는 실행 인자(1 또는 2, 토픽 파티션이 2개라 2를 넘으면 의미 없음)로 정하고 결과에 적는다. 원래 replicas를 기록하고 끝나면 되돌린다.
- k6 v2.3.0 constant-arrival-rate는 도착 간격이 일정해 포아송보다 몰림이 덜하다.

## 3. 판정 기준(회차마다 모두 만족, {a.runs}회 모두 통과해야 통과)

| # | 기준 | 값 | 재는 법 |
| --- | --- | --- | --- |
| C1 | 조회 지연 | p95 ≤ 300ms, p99 ≤ 500ms | k6 `http_req_duration{{scenario:read, phase:main}}`(사용자 결정 예산과 같음) |
| C2 | 전체 오류율 | ≤ 0.1% | 두 시나리오 합 `http_req_failed{{phase:main}}`(조회 200·업로드 202 아니면 오류) |
| C3 | 업로드 반영 지연 | **p95 ≤ {a.reflect_p95:g}초(추천, 조율 세션 확정 필요)** | 묶음별 전송 시각(k6 응답 끝 − 요청 시간) → `activity_batch_receipts.processed_at`. 유예 뒤까지 반영 안 된 묶음은 무한대로 센다 |
| C4 | 미처리 기울기 | 0(OLS 기울기의 95% 신뢰구간 하한 ≤ 0) | 실행 중 30초마다 "지금까지 보냈을 묶음 − 반영된 묶음" 표본, 웜업 뒤 표본의 시간 회귀. 양수로 확실하면 "처리 못 따라감" |
| C5 | 부하 발생기 | k6 `dropped_iterations` 0 | 시나리오별 |
| C6 | 도착률 | 목표의 ±2% | 웜업 뒤 시나리오별 시작한 요청 수 ÷ 시간 |

C3 추천 근거: 오늘 화면은 보이는 동안 60초마다 새로고침한다(사용자 결정). 업로드가 다음 새로고침에 거의 늘 보이려면 반영 지연이 새로고침 간격보다 충분히 짧아야 한다. 클라이언트는 이미 10초 창으로 묶어 최대 약 10초 늦게 보낸다. 서버 쪽 반영 p95를 10초로 두면 "관찰 → 화면" 지연이 최악에도 묶음 10초 + 반영 10초 + 새로고침 60초 안이다. 로컬 검증의 반영 p95는 수십 ms였다.

보고만: 업로드 HTTP 지연 p50·p95·p99, 반영 지연 p50·p99, 단계 끝·유예 뒤 미처리, k6 메모리, 오류 상태 코드 분포.
"""
    Path(a.out_dir, name + ".md").write_text(md)
    print(
        json.dumps(
            {
                k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in w.items()
                if k != "criteria"
            },
            ensure_ascii=False,
        )
    )


# --------------------------------------------------------------------------- 준비


def cmd_tokens(a) -> None:
    inv = json.loads(Path(a.inventory).read_text())
    excluded = {x for x in a.exclude.split(",") if x}
    today = inv["today"]
    accs = [
        {
            "token": token_for(x["account_id"]),
            "past": [d for d in x["dates"] if d < today],
        }
        for x in inv["accounts"]
        if x["account_id"] not in excluded
    ]
    Path(a.out).write_text(json.dumps({"today": today, "accounts": accs}))
    print(json.dumps({"tokens": len(accs), "today": today, "out": a.out}))


Q_TARGETS = """
SELECT DISTINCT ON (d.account_id) d.account_id, d.id AS device_id,
       (SELECT max(sequence) FROM activity_records r WHERE r.device_id = d.id) AS seq
FROM devices d WHERE EXISTS (SELECT 1 FROM activity_records r WHERE r.device_id = d.id)
ORDER BY d.account_id, d.id"""
Q_TEMPLATE = "SELECT payload FROM activity_records WHERE record_type = 'activity_observation' LIMIT 50"


@dataclass
class Targets:
    """묶음을 보낼 기기(devices.json)·context와, 기기마다 이어 쓸 sequence·전역 순번."""

    dev: dict
    contexts: list
    seqs: list[int]
    g: int


async def fetch_targets(excluded: set[str]) -> tuple[list[dict], list]:
    rc = H.Reconn(env_dsn())
    targets = [
        dict(t)
        for t in await rc.run(lambda c: c.fetch(Q_TARGETS), H.SQL_T)
        if str(t["account_id"]) not in excluded
    ]
    contexts = []
    for r in await rc.run(lambda c: c.fetch(Q_TEMPLATE), H.SQL_T):
        p = r["payload"]
        contexts.append(p.get("context", p) if isinstance(p, dict) else p)
    await rc.close()
    return targets, contexts


def load_targets(out: Path, exclude: str) -> Targets:
    """기기·토큰·context를 한 번 만들고(devices.json·contexts.json), 기기 sequence는 DB 최대값과
    앞선 준비가 남긴 seq-state.json 중 큰 쪽에서 잇는다(계단용과 mixed용 묶음이 겹치지 않게)."""
    excluded = {x for x in exclude.split(",") if x}
    dev: dict[str, Any]
    if (out / "devices.json").exists():
        dev = json.loads((out / "devices.json").read_text())
        contexts = json.loads((out / "contexts.json").read_text())["contexts"]
    else:
        targets, contexts = asyncio.run(fetch_targets(excluded))
        dev = {
            "devices": [str(t["device_id"]) for t in targets],
            "accounts": [str(t["account_id"]) for t in targets],
            "tokens": [token_for(str(t["account_id"])) for t in targets],
            "db_seq": [t["seq"] for t in targets],
        }
        (out / "devices.json").write_text(json.dumps(dev))
        (out / "contexts.json").write_text(json.dumps({"contexts": contexts}))
    state = (
        json.loads((out / "seq-state.json").read_text())
        if (out / "seq-state.json").exists()
        else {}
    )
    seqs = [
        max(dev["db_seq"][i], state.get("seqs", [0] * len(dev["devices"]))[i])
        for i in range(len(dev["devices"]))
    ]
    return Targets(dev, contexts, seqs, state.get("g", 0))


def gen_payloads(
    out: Path, stages: list[tuple[str, float, int]], *, targets: Targets, seed: int
) -> dict:
    """stages = [(이름, rate, 행 수)]. 관찰 시각은 t0 + 전역 순번 × 2ms(묶음 안 두 번째 기록 +1ms), sequence는 기기마다
    순번 순서 → 같은 기기에서 sequence와 관찰 시각이 함께 는다(보내는 순서·동시성과 상관없이 consumer 순서 검사 통과)."""
    dev, contexts, seqs, g = targets.dev, targets.contexts, targets.seqs, targets.g
    rng = random.Random(seed + g)
    p2 = RECORDS_PER_BATCH - 1
    t0 = int(utc_now().timestamp() * 1000)
    plan = {}
    for name, rate, n in stages:
        rows = []
        for _ in range(n):
            di = rng.randrange(len(dev["devices"]))
            k = 2 if rng.random() < p2 else 1
            rows.append([di, seqs[di] + 1, k, rng.randrange(len(contexts))])
            seqs[di] += k
        u, u2 = uuid.uuid4().hex, uuid.uuid4().hex
        st = {
            "name": name,
            "rate": rate,
            "t0": t0 + 2 * g,
            "bid_prefix": f"{u[0:8]}-{u[8:12]}-4{u[13:16]}-8{u[17:20]}-",
            "eid_prefix": f"{u2[0:8]}-{u2[8:12]}-4{u2[13:16]}-8{u2[17:20]}-",
            "rows": rows,
        }
        g += n
        (out / f"payloads-{name}.rows.json").write_text(
            json.dumps(st, separators=(",", ":"))
        )
        chunks = [
            ";".join(",".join(map(str, r)) for r in rows[i : i + CHUNK])
            for i in range(0, len(rows), CHUNK)
        ]
        k6st: dict[str, Any] = {k: v for k, v in st.items() if k != "rows"}
        k6st.update(chunk=CHUNK, n=len(rows), chunks=chunks)
        (out / f"payloads-{name}.json").write_text(
            json.dumps(k6st, separators=(",", ":"))
        )
        plan[name] = {
            "rate": round(rate, 3),
            "rows": n,
            "file": f"payloads-{name}.json",
        }
    (out / "seq-state.json").write_text(json.dumps({"seqs": seqs, "g": g}))
    return plan


def cmd_payloads(a) -> None:
    # 한 번만 만든다. 업로드가 밀린 상태에서 다시 만들면 아직 처리되지 않은 sequence를 다른 내용으로 다시 써
    # consumer가 종료할 수 있다(로컬 검증에서 확인). 다시 하려면 consumer 밀림이 0인지 보고 --force로.
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "payload-plan.json").exists() and not a.force:
        sys.exit("payloads: 이미 준비돼 있다(payload-plan.json). 다시 만들려면 --force")
    targets = load_targets(out, a.exclude)
    base = a.base_rps or expected_upload(a.dau)["avg_batches_per_s"]
    stages = [
        (f"x{m:g}", base * m, math.ceil(base * m * a.stage_sec * 1.1))
        for m in [float(x) for x in a.stages.split(",")]
    ]
    plan = gen_payloads(out, stages, targets=targets, seed=a.seed)
    (out / "payload-plan.json").write_text(
        json.dumps(
            {
                "base_batches_per_s": base,
                "expected": expected_upload(a.dau),
                "stages": plan,
            },
            indent=1,
        )
    )
    print(
        json.dumps(
            {
                "targets": len(targets.dev["devices"]),
                "contexts": len(targets.contexts),
                "stages": plan,
            }
        )
    )


def cmd_payloads_mixed(a) -> None:
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if (out / "mixed-plan.json").exists() and not a.force:
        sys.exit(
            "payloads-mixed: 이미 준비돼 있다(mixed-plan.json). 다시 만들려면 --force"
        )
    w = json.loads(Path(a.workload).read_text())
    total = a.total or w["total_rps"]
    write_rps = total * (1 - w["read_share"])
    targets = load_targets(out, a.exclude)
    n = math.ceil(write_rps * a.duration * 1.1)
    stages = [(f"mixed-r{k}", write_rps, n) for k in range(1, a.runs + 1)]
    plan = gen_payloads(out, stages, targets=targets, seed=a.seed + 1000)
    (out / "mixed-plan.json").write_text(
        json.dumps(
            {
                "total_rps": total,
                "read_rps": total * w["read_share"],
                "write_rps": write_rps,
                "duration_s": a.duration,
                "stages": plan,
            },
            indent=1,
        )
    )
    print(
        json.dumps(
            {
                "total_rps": total,
                "write_rps": round(write_rps, 3),
                "rows_per_run": n,
                "runs": a.runs,
            }
        )
    )


# --------------------------------------------------------------------------- 분석 공통


def read_k6_csv(path: Path):
    """k6 --out csv(gzip, K6_CSV_TIME_FORMAT=unix_milli). 요청(http_req_duration)·실패(http_req_failed)·dropped_iterations."""
    reqs, failed, dropped = [], [], Counter()
    with gzip.open(path, "rt", newline="") as f:
        for row in csv.DictReader(f):
            m = row["metric_name"]
            if m not in ("http_req_duration", "http_req_failed", "dropped_iterations"):
                continue
            tags = dict(
                x.split("=", 1)
                for x in row.get("extra_tags", "").split("&")
                if "=" in x
            )
            sc = row.get("scenario", "")
            if m == "dropped_iterations":
                dropped[sc] += int(float(row["metric_value"]))
            elif m == "http_req_duration":
                reqs.append(
                    {
                        "t_end_ms": float(row["timestamp"]),
                        "ms": float(row["metric_value"]),
                        "scenario": sc,
                        "status": row.get("status", ""),
                        "phase": tags.get("phase", ""),
                        "bid": tags.get("bid"),
                        "error_code": row.get("error_code", ""),
                    }
                )
            else:
                failed.append((sc, tags.get("phase", ""), float(row["metric_value"])))
    return reqs, failed, dropped


def stage_stats(
    stage: str, d: Path, *, rate: float, stage_sec: float, warm: float
) -> tuple[dict[str, Any], list]:
    reqs, failed, dropped = read_k6_csv(d / f"{stage}.csv.gz")
    main = [r for r in reqs if r["phase"] == "main"]
    lat = [r["ms"] for r in main]
    fails = [v for _, p, v in failed if p == "main"]
    summary = (
        json.loads((d / f"{stage}.summary.json").read_text())
        if (d / f"{stage}.summary.json").exists()
        else {}
    )
    window = max(1e-9, stage_sec - warm)
    ok = sum(1 for r in main if r["status"] in ("200", "202"))
    err_rate = (sum(fails) / len(fails)) if fails else None
    st: dict[str, Any] = {
        "stage": stage,
        "rate": rate,
        "n": len(main),
        "ok": ok,
        "done_rps": round(ok / window, 2),
        "p50_ms": r1(rank(lat, 0.5)),
        "p95_ms": r1(rank(lat, 0.95)),
        "p99_ms": r1(rank(lat, 0.99)),
        "error_rate": None if err_rate is None else round(err_rate, 4),
        "status": dict(Counter(r["status"] or f"err{r['error_code']}" for r in main)),
        "dropped_iterations": sum(dropped.values()),
        # summary-export의 thresholds 값: true = 그 threshold 실패(abortOnFail이면 k6가 중단, 종료 코드 99)
        "aborted_by_threshold": any(
            v is True or (isinstance(v, dict) and not v.get("ok", True))
            for m in summary.get("metrics", {}).values()
            for v in (m.get("thresholds") or {}).values()
        ),
    }
    st["generator_saturated"] = st["dropped_iterations"] > 0
    st["within_budget"] = (
        not st["generator_saturated"]
        and err_rate is not None
        and err_rate < ERROR_BUDGET
        and st["p95_ms"] is not None
        and st["p95_ms"] <= READ_P95_BUDGET_MS
        and st["p99_ms"] <= READ_P99_BUDGET_MS
    )
    st["stop"] = (
        err_rate is None
        or err_rate > STOP_ERROR_RATE
        or (st["p99_ms"] or 0) > STOP_P99_MS
        or st["aborted_by_threshold"]
    )
    return st, reqs


Q_RECEIPTS = """
SELECT r.batch_id, extract(epoch FROM r.processed_at) * 1000 AS processed_ms FROM activity_batch_receipts r
JOIN unnest($1::uuid[], $2::uuid[], $3::uuid[]) AS k(account_id, device_id, batch_id)
  ON r.account_id = k.account_id AND r.device_id = k.device_id AND r.batch_id = k.batch_id"""


async def receipts(keys: list[tuple], rc=None) -> dict:
    own = rc is None
    rc = rc or H.Reconn(env_dsn())
    got = {}
    for i in range(0, len(keys), 2000):
        ch = keys[i : i + 2000]
        rows = await rc.run(
            lambda c, ch=ch: c.fetch(
                Q_RECEIPTS,
                [uuid.UUID(x[0]) for x in ch],
                [uuid.UUID(x[1]) for x in ch],
                [uuid.UUID(x[2]) for x in ch],
            ),
            H.LONG_T,
        )
        got.update({str(r["batch_id"]): float(r["processed_ms"]) for r in rows})
    if own:
        await rc.close()
    return got


def batch_keys(d: Path, stage: str) -> list[tuple]:
    """묶음 순번 i → (account_id, device_id, batch_id). k6와 같은 규칙(접두사 + 16진 12자리)."""
    pay = json.loads((d / f"payloads-{stage}.rows.json").read_text())
    dev = json.loads((d / "devices.json").read_text())
    return [
        (dev["accounts"][r[0]], dev["devices"][r[0]], f"{pay['bid_prefix']}{i:012x}")
        for i, r in enumerate(pay["rows"])
    ]


def cmd_report_read(a) -> None:
    d = Path(a.dir)
    st, _ = stage_stats(a.stage, d, rate=a.rate, stage_sec=a.stage_sec, warm=a.warm)
    with open(d / "stages-read.jsonl", "a") as f:
        f.write(json.dumps(st, ensure_ascii=False) + "\n")
    print(json.dumps(st, ensure_ascii=False))
    sys.exit(3 if st["stop"] else 0)


def cmd_report_write(a) -> None:
    d = Path(a.dir)
    st, reqs = stage_stats(a.stage, d, rate=a.rate, stage_sec=a.stage_sec, warm=a.warm)
    by_bid = {k[2]: k for k in batch_keys(d, a.stage)}
    sent = [r for r in reqs if r["status"] == "202" and r["bid"] in by_bid]
    keys = [by_bid[r["bid"]] for r in sent]
    if a.when == "end":  # 단계 끝 바로 뒤 미처리
        got = asyncio.run(receipts(keys))
        st2 = {
            "stage": a.stage,
            "unprocessed_end": len(keys) - len(got),
            "sent_202": len(keys),
        }
        (d / f"{a.stage}.backlog-end.json").write_text(json.dumps(st2))
        print(json.dumps(st2))
        return
    got = asyncio.run(receipts(keys))  # 유예 뒤
    # 처리 완료 지연 = 전송 시각(응답 끝 − 요청 시간) → processed_at(DB 시계. Pod·RDS 모두 NTP 동기라 가정)
    proc = [
        got[r["bid"]] - (r["t_end_ms"] - r["ms"])
        for r in sent
        if r["bid"] in got and r["phase"] == "main"
    ]
    end = (
        json.loads((d / f"{a.stage}.backlog-end.json").read_text())
        if (d / f"{a.stage}.backlog-end.json").exists()
        else {}
    )
    st.update(
        sent_202=len(keys),
        unprocessed_end=end.get("unprocessed_end"),
        unprocessed_after_grace=len(keys) - len(got),
        proc_p50_ms=r1(rank(proc, 0.5)),
        proc_p95_ms=r1(rank(proc, 0.95)),
        proc_p99_ms=r1(rank(proc, 0.99)),
    )
    st["backlog_growing"] = st["unprocessed_after_grace"] > 2 * a.rate
    if st["backlog_growing"]:
        st["stop"] = True
    with open(d / "stages-write.jsonl", "a") as f:
        f.write(json.dumps(st, ensure_ascii=False) + "\n")
    print(json.dumps(st, ensure_ascii=False))
    sys.exit(3 if st["stop"] else 0)


def cmd_summary(a) -> None:
    d = Path(a.dir)
    p = d / f"stages-{a.kind}.jsonl"
    stages = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    budget = [s["rate"] for s in stages if s.get("within_budget")]
    err = next((s["rate"] for s in stages if (s.get("error_rate") or 0) > 0), None)
    out = {
        "kind": a.kind,
        "k6": a.k6_version,
        "max_rps_within_budget": max(budget) if budget else None,
        "first_error_rps": err,
        "stages": stages,
    }
    if a.kind == "write" and (d / "payload-plan.json").exists():
        out["plan"] = json.loads((d / "payload-plan.json").read_text())
    (d / f"{a.kind}-summary.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False)
    )
    head = [
        "stage",
        "rate",
        "n",
        "done_rps",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "error_rate",
        "status",
        "dropped_iterations",
        "generator_saturated",
        "within_budget",
        "stop",
    ]
    if a.kind == "write":
        head += [
            "proc_p50_ms",
            "proc_p95_ms",
            "proc_p99_ms",
            "unprocessed_end",
            "unprocessed_after_grace",
            "backlog_growing",
        ]
    lines = [
        f"## API 한계 측정: {a.kind} (k6 {a.k6_version}, 보고용·사전 등록 밖)",
        "",
        f"- 예산(p95 ≤ 300ms·p99 ≤ 500ms·오류 < 1%·dropped_iterations 0) 안에서 버틴 최대 rps: **{out['max_rps_within_budget']}**",
        f"- 오류가 시작된 rps: **{err}**",
        "- k6 ramping-arrival-rate는 도착 간격이 일정해 하네스(포아송)보다 몰림이 덜하다. dropped_iterations > 0 단계는 발생기 포화로 표시",
        "",
        "| " + " | ".join(head) + " |",
        "|" + " --- |" * len(head),
    ]
    lines += ["| " + " | ".join(str(s.get(h, "")) for h in head) + " |" for s in stages]
    (d / f"{a.kind}-summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


# --------------------------------------------------------------------------- mixed: 미처리 감시와 판정


def append_line(path: Path, line: str) -> None:
    with path.open("a") as f:
        f.write(line + "\n")


async def cmd_backlog_watch(a) -> None:
    """start-file(epoch 초, k6 시작 직전 sh가 씀)부터 interval초마다: 지금까지 보냈을 묶음(경과 × rate, 5초 여유를 뺌) 중
    영수증이 없는 수. 이미 반영된 묶음은 다시 묻지 않는다(DB 부하를 작게). stop-file이 생기면 끝낸다."""
    d = Path(a.dir)
    keys = await asyncio.to_thread(batch_keys, d, a.stage)
    start = float((await asyncio.to_thread(Path(a.start_file).read_text)).strip())
    done: set[str] = set()
    rc = H.Reconn(env_dsn())
    out = d / f"backlog-{a.stage}.jsonl"
    while not await asyncio.to_thread(Path(a.stop_file).exists):
        await asyncio.sleep(a.interval)
        t = utc_now().timestamp()
        if a.duration and t - start > a.duration + 5:
            break  # 실행이 끝난 뒤에는 "보냈을 묶음"을 추정할 수 없다(준비량은 1.1배). 유예 뒤 미반영은 report-mixed가 실제 전송으로 센다
        upto = min(len(keys), max(0, int((t - start - 5) * a.rate)))
        todo = [k for k in keys[:upto] if k[2] not in done]
        try:
            got = await receipts(todo, rc)
            done.update(got)
            rec = {
                "t": round(t - start, 1),
                "sent_est": upto,
                "unprocessed": upto - len(done),
            }
        except H.CONN_ERRORS as exc:
            rec = {"t": round(t - start, 1), "error": f"{type(exc).__name__}"}
        await asyncio.to_thread(append_line, out, json.dumps(rec))
    await rc.close()


def ols_slope(pts: list[tuple[float, float]]):
    """(기울기, 95% 신뢰구간 하한, 상한). 표본 3개 미만이면 None."""
    n = len(pts)
    if n < MIN_OLS_POINTS:
        return None
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in pts) / sxx
    resid = sum((y - (my + b * (x - mx))) ** 2 for x, y in pts)
    se = math.sqrt(resid / (n - 2) / sxx)  # n ≥ MIN_OLS_POINTS
    tcrit = {
        1: 12.71,
        2: 4.30,
        3: 3.18,
        4: 2.78,
        5: 2.57,
        6: 2.45,
        7: 2.36,
        8: 2.31,
        9: 2.26,
        10: 2.23,
    }.get(n - 2, 2.0)
    return b, b - tcrit * se, b + tcrit * se


def cmd_report_mixed(a) -> None:
    d = Path(a.dir)
    w = json.loads(Path(a.workload).read_text())
    crit = w["criteria"]
    reqs, failed, dropped = read_k6_csv(d / f"{a.stage}.csv.gz")
    window = max(1e-9, a.duration - a.warm)
    total = a.total or w["total_rps"]
    target = {"read": total * w["read_share"], "write": total * (1 - w["read_share"])}
    res = {
        "stage": a.stage,
        "total_rps": total,
        "duration_s": a.duration,
        "consumers": a.consumers,
        "scenarios": {},
    }
    for sc in ("read", "write"):
        main = [r for r in reqs if r["scenario"] == sc and r["phase"] == "main"]
        lat = [r["ms"] for r in main]
        res["scenarios"][sc] = {
            "target_rps": round(target[sc], 3),
            "n": len(main),
            "arrival_rps": round(len(main) / window, 3),
            "arrival_err": round(len(main) / window / target[sc] - 1, 4)
            if target[sc]
            else None,
            "p50_ms": r1(rank(lat, 0.5)),
            "p95_ms": r1(rank(lat, 0.95)),
            "p99_ms": r1(rank(lat, 0.99)),
            "status": dict(
                Counter(r["status"] or f"err{r['error_code']}" for r in main)
            ),
            "dropped_iterations": dropped.get(sc, 0),
        }
    fails = [v for _, p, v in failed if p == "main"]
    err_rate = sum(fails) / len(fails) if fails else None
    by_bid = {k[2]: k for k in batch_keys(d, a.stage)}
    sent = [
        r
        for r in reqs
        if r["scenario"] == "write"
        and r["phase"] == "main"
        and r["status"] == "202"
        and r["bid"] in by_bid
    ]
    got = asyncio.run(receipts([by_bid[r["bid"]] for r in sent]))
    lag = [
        (got[r["bid"]] - (r["t_end_ms"] - r["ms"])) / 1000
        if r["bid"] in got
        else math.inf
        for r in sent
    ]
    bl = (
        [
            json.loads(x)
            for x in (d / f"backlog-{a.stage}.jsonl").read_text().splitlines()
        ]
        if (d / f"backlog-{a.stage}.jsonl").exists()
        else []
    )
    pts = [
        (x["t"], x["unprocessed"])
        for x in bl
        if "unprocessed" in x and a.warm <= x["t"] <= a.duration + 5
    ]
    slope = ols_slope(pts)
    res.update(
        error_rate=None if err_rate is None else round(err_rate, 5),
        reflect_s={
            "p50": r3(rank(lag, 0.5)),
            "p95": r3(rank(lag, 0.95)),
            "p99": r3(rank(lag, 0.99)),
            "unreflected_after_grace": sum(1 for x in lag if x == math.inf),
            "sent_202": len(sent),
        },
        backlog_samples=len(pts),
        backlog_last=pts[-1][1] if pts else None,
        backlog_slope=None
        if slope is None
        else {
            "per_s": round(slope[0], 4),
            "ci95": [round(slope[1], 4), round(slope[2], 4)],
        },
    )
    rd, wr = res["scenarios"]["read"], res["scenarios"]["write"]
    checks = {
        "C1_read_latency": rd["p95_ms"] is not None
        and rd["p95_ms"] <= crit["read_p95_ms"]
        and rd["p99_ms"] <= crit["read_p99_ms"],
        "C2_error_rate": err_rate is not None and err_rate <= crit["error_rate_max"],
        "C3_reflect_p95": res["reflect_s"]["p95"] is not None
        and res["reflect_s"]["p95"] <= crit["reflect_p95_s"],
        "C4_backlog_slope": slope is not None and slope[1] <= 0,
        "C5_dropped": rd["dropped_iterations"] == 0 and wr["dropped_iterations"] == 0,
        "C6_arrival": all(
            s["arrival_err"] is not None
            and abs(s["arrival_err"]) <= crit["arrival_tolerance"]
            for s in (rd, wr)
        ),
    }
    res["checks"] = checks
    res["pass"] = all(checks.values())
    res["backlog_note"] = "처리 못 따라감" if slope is not None and slope[1] > 0 else ""
    (d / f"mixed-{a.stage}.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False)
    )
    md = [
        f"### {a.stage}: {total:g} rps × {a.duration / 60:g}분, consumer {a.consumers} → **{'통과' if res['pass'] else '실패'}**",
        "",
        "| 기준 | 결과 | 값 |",
        "| --- | --- | --- |",
        f"| C1 조회 p95/p99 ≤ {crit['read_p95_ms']}/{crit['read_p99_ms']}ms | {'통과' if checks['C1_read_latency'] else '실패'} | {rd['p95_ms']} / {rd['p99_ms']}ms (n {rd['n']}) |",
        f"| C2 전체 오류율 ≤ {crit['error_rate_max'] * 100:g}% | {'통과' if checks['C2_error_rate'] else '실패'} | {res['error_rate']} |",
        f"| C3 반영 지연 p95 ≤ {crit['reflect_p95_s']:g}초 | {'통과' if checks['C3_reflect_p95'] else '실패'} | p50 {res['reflect_s']['p50']} · p95 {res['reflect_s']['p95']} · p99 {res['reflect_s']['p99']}초, 유예 뒤 미반영 {res['reflect_s']['unreflected_after_grace']}/{len(sent)} |",
        f"| C4 미처리 기울기 0 | {'통과' if checks['C4_backlog_slope'] else '실패'} | {res['backlog_slope']} (표본 {len(pts)}, 마지막 {res['backlog_last']}) {res['backlog_note']} |",
        f"| C5 dropped_iterations 0 | {'통과' if checks['C5_dropped'] else '실패'} | read {rd['dropped_iterations']} · write {wr['dropped_iterations']} |",
        f"| C6 도착률 ±{crit['arrival_tolerance'] * 100:g}% | {'통과' if checks['C6_arrival'] else '실패'} | read {rd['arrival_rps']}/{rd['target_rps']} ({rd['arrival_err']}) · write {wr['arrival_rps']}/{wr['target_rps']} ({wr['arrival_err']}) |",
        "",
        f"보고만: 업로드 HTTP p50/p95/p99 {wr['p50_ms']}/{wr['p95_ms']}/{wr['p99_ms']}ms, 상태 read {rd['status']} · write {wr['status']}",
        "",
    ]
    (d / f"mixed-{a.stage}.md").write_text("\n".join(md))
    print("\n".join(md))


# --------------------------------------------------------------------------- main


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan")
    s.add_argument("--dau", type=int, default=1000)
    s = sub.add_parser("workload")
    s.add_argument("--total", type=float, default=100)
    s.add_argument("--out-dir", default=".")
    s.add_argument("--duration", type=int, default=900)
    s.add_argument("--runs", type=int, default=2)
    s.add_argument("--warm", type=float, default=60)
    s.add_argument("--grace", type=float, default=60)
    s.add_argument("--reflect-p95", type=float, default=10)
    s = sub.add_parser("tokens")
    s.add_argument("--inventory", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--exclude", default="")
    s = sub.add_parser("payloads")
    s.add_argument("--out-dir", required=True)
    s.add_argument("--exclude", default="")
    s.add_argument("--stages", default="1,2,4,8,16")
    s.add_argument("--stage-sec", type=float, default=180)
    s.add_argument("--base-rps", type=float, default=0)
    s.add_argument("--dau", type=int, default=1000)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("payloads-mixed")
    s.add_argument("--out-dir", required=True)
    s.add_argument("--exclude", default="")
    s.add_argument("--workload", required=True)
    s.add_argument("--total", type=float, default=0)
    s.add_argument("--duration", type=float, default=900)
    s.add_argument("--runs", type=int, default=2)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--force", action="store_true")
    for name in ("report-read", "report-write"):
        s = sub.add_parser(name)
        s.add_argument("stage")
        s.add_argument("--rate", type=float, required=True)
        s.add_argument("--dir", required=True)
        s.add_argument("--stage-sec", type=float, default=180)
        s.add_argument("--warm", type=float, default=30)
    sub.choices["report-write"].add_argument(
        "--when", choices=["end", "final"], default="final"
    )
    s = sub.add_parser("summary")
    s.add_argument("kind", choices=["read", "write"])
    s.add_argument("--dir", required=True)
    s.add_argument("--k6-version", default="")
    s = sub.add_parser("backlog-watch")
    s.add_argument("stage")
    s.add_argument("--dir", required=True)
    s.add_argument("--rate", type=float, required=True)
    s.add_argument("--start-file", required=True)
    s.add_argument("--stop-file", required=True)
    s.add_argument("--interval", type=float, default=30)
    s.add_argument(
        "--duration",
        type=float,
        default=0,
        help="k6 실행 시간(초). 이 뒤로는 표본을 남기지 않는다",
    )
    s = sub.add_parser("report-mixed")
    s.add_argument("stage")
    s.add_argument("--dir", required=True)
    s.add_argument("--workload", required=True)
    s.add_argument("--total", type=float, default=0)
    s.add_argument("--duration", type=float, default=900)
    s.add_argument("--warm", type=float, default=60)
    s.add_argument("--consumers", default="?")
    a = p.parse_args()
    if a.cmd == "plan":
        print(json.dumps(expected_upload(a.dau)))
    elif a.cmd == "workload":
        cmd_workload(a)
    elif a.cmd == "tokens":
        cmd_tokens(a)
    elif a.cmd == "payloads":
        cmd_payloads(a)
    elif a.cmd == "payloads-mixed":
        cmd_payloads_mixed(a)
    elif a.cmd == "report-read":
        cmd_report_read(a)
    elif a.cmd == "report-write":
        cmd_report_write(a)
    elif a.cmd == "backlog-watch":
        asyncio.run(cmd_backlog_watch(a))
    elif a.cmd == "report-mixed":
        cmd_report_mixed(a)
    else:
        cmd_summary(a)


if __name__ == "__main__":
    main()

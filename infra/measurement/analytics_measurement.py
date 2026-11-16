"""측정 B(새 분석 API·일간 통계 batch) — k6 입력, batch 반복 실행, 회차 판정, 전체 요약. Pod 안에서 실행한다.

기준은 measurement-b.json(고정 문서)이다. 측정 A 도구(k6_inputs_and_reports.py)의 k6 CSV 읽기·영수증 조회·
기울기 계산을 그대로 쓴다.

  tokens-b     tokens-b.json: 새 API 조회용 계정 토큰, 서버 실제 오늘, 시드 날짜, 완료 세션, 구간 많은 계정 토큰
  batch-loop   일간 통계 job을 회차 동안 주기마다 실행하고 b-batch.txt에 한 줄씩 남긴다
  report-b     회차 하나의 판정(mixed-b-r<n>.json·.md)
  summary-b    전체 요약(summary-b.json·.md): 회차 판정, G1 결론, 구간 수별 조회 로그, 환산표, batch·되돌리기 기록
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import sys
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from mosemo.platform.clock import utc_now

HERE = Path(__file__).resolve().parent
BUCKETS = ("small", "1000", "5000", "10000")
SMALL_LIMIT = 1000
MS_FIELDS = ("source_read_ms", "snapshot_build_ms", "compute_ms")
SESSIONS_LIMIT = 5000
RUNS_PER_KIND = 2


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass가 모듈을 sys.modules에서 찾는다
    spec.loader.exec_module(module)
    return module


K = _load("k6_inputs_and_reports")
H = K.H


def load_plan(path: str) -> dict:
    return json.loads(Path(path).read_text())


def k6_rate(rps: float) -> float:
    """k6-analytics-mixed.js는 timeUnit 100s에 rate = round(rps × 100)으로 준다."""
    return max(1, round(rps * 100)) / 100


# --------------------------------------------------------------------------- tokens-b

Q_SESSIONS = """
SELECT account_id, id FROM focus_sessions
WHERE ended_at IS NOT NULL AND NOT (account_id = ANY($1::uuid[]))
ORDER BY id LIMIT $2"""


async def completed_sessions(excluded: list[str]) -> list[tuple[str, str]]:
    rc = H.Reconn(K.env_dsn())
    rows = await rc.run(
        lambda c: c.fetch(Q_SESSIONS, [uuid.UUID(x) for x in excluded], SESSIONS_LIMIT),
        H.SQL_T,
    )
    await rc.close()
    return [(str(r["account_id"]), str(r["id"])) for r in rows]


def cmd_tokens_b(a: argparse.Namespace) -> None:
    plan = load_plan(a.plan)
    inv = json.loads(Path(a.inventory).read_text())
    dense = json.loads(Path(a.dense).read_text())
    excluded = [x for x in a.exclude.split(",") if x] + list(dense.values())
    accounts = [
        x["account_id"] for x in inv["accounts"] if x["account_id"] not in excluded
    ]
    index = {acc: i for i, acc in enumerate(accounts)}
    sessions = [
        [index[acc], sid]
        for acc, sid in asyncio.run(completed_sessions(excluded))
        if acc in index
    ]
    today = utc_now().astimezone(ZoneInfo(plan["timezone"])).date().isoformat()
    out = {
        "today": today,
        "seed_dates": plan["seed_dates"],
        "today_share": plan["stats_today_share"],
        "tokens": [K.token_for(acc) for acc in accounts],
        "sessions": sessions,
        "heavy": K.token_for(dense[str(plan["heavy_segments"])]),
        "probes": [K.token_for(dense[n]) for n in sorted(plan["probe_accounts"])],
    }
    Path(a.out).write_text(json.dumps(out))
    print(
        json.dumps(
            {"today": today, "accounts": len(accounts), "sessions": len(sessions)}
        )
    )
    if not sessions:
        sys.exit("완료 세션이 없다(sessions 시나리오를 만들 수 없다)")


# --------------------------------------------------------------------------- batch-loop


def saved_from_output(text: str) -> str:
    """job 출력의 `batch job completed` JSON 줄에서 saved. 없으면 NA."""
    for line in reversed(text.splitlines()):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and rec.get("event") == "batch job completed":
            return str(rec.get("saved", "NA"))
    return "NA"


def stamp(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_job_once(job: str, *, log_dir: Path, tag: str) -> str:
    started = utc_now()
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mosemo.entrypoints.batch",
        "run",
        job,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = out.decode(errors="replace")
    await asyncio.to_thread((log_dir / f"batch-{tag}.log").write_text, text)
    return (
        f"{stamp(started)} {stamp(utc_now())} rc={proc.returncode} "
        f"saved={saved_from_output(text)}"
    )


async def cmd_batch_loop(a: argparse.Namespace) -> None:
    """k6 시작과 함께 한 번, 이후 interval초마다. 앞 실행이 끝나지 않았으면 그 차례를 건너뛰고 기록한다.
    stop-file이 생기면 새 실행을 시작하지 않고 돌고 있는 실행을 기다린 뒤 끝낸다."""
    out = Path(a.out)
    log_dir = Path(a.log_dir)
    await asyncio.to_thread(log_dir.mkdir, parents=True, exist_ok=True)
    start = utc_now()
    running: asyncio.Task[str] | None = None
    k = 0
    while True:
        due = (utc_now() - start).total_seconds() >= k * a.interval
        if await asyncio.to_thread(Path(a.stop_file).exists):
            break
        if due:
            if running is not None and not running.done():
                line = f"batch-skip r{a.round} {stamp(utc_now())} 앞 실행이 아직 돈다"
                await asyncio.to_thread(K.append_line, out, line)
            else:
                if running is not None:
                    line = f"batch r{a.round} {running.result()}"
                    await asyncio.to_thread(K.append_line, out, line)
                running = asyncio.create_task(
                    run_job_once(a.job, log_dir=log_dir, tag=f"r{a.round}-{k}")
                )
            k += 1
        await asyncio.sleep(1)
    if running is not None:
        line = f"batch r{a.round} {await running}"
        await asyncio.to_thread(K.append_line, out, line)


# --------------------------------------------------------------------------- report-b


def scenario_stats(
    reqs: list[dict], sc: str, *, target: float, window: float, dropped: Counter
) -> dict[str, Any]:
    main = [r for r in reqs if r["scenario"] == sc and r["phase"] == "main"]
    lat = [r["ms"] for r in main]
    return {
        "target_rps": round(target, 3),
        "n": len(main),
        "arrival_rps": round(len(main) / window, 3),
        "arrival_err": round(len(main) / window / target - 1, 4) if target else None,
        "p50_ms": K.r1(K.rank(lat, 0.5)),
        "p95_ms": K.r1(K.rank(lat, 0.95)),
        "p99_ms": K.r1(K.rank(lat, 0.99)),
        "status": dict(Counter(r["status"] or f"err{r['error_code']}" for r in main)),
        "dropped_iterations": dropped.get(sc, 0),
    }


def reflect(d: Path, stage: str, *, reqs: list[dict]) -> dict[str, Any]:
    by_bid = {k[2]: k for k in K.batch_keys(d, stage)}
    sent = [
        r
        for r in reqs
        if r["scenario"] == "write"
        and r["phase"] == "main"
        and r["status"] == "202"
        and r["bid"] in by_bid
    ]
    got = asyncio.run(K.receipts([by_bid[r["bid"]] for r in sent]))
    lag = [
        (got[r["bid"]] - (r["t_end_ms"] - r["ms"])) / 1000
        if r["bid"] in got
        else math.inf
        for r in sent
    ]
    return {
        "p50": K.r3(K.rank(lag, 0.5)),
        "p95": K.r3(K.rank(lag, 0.95)),
        "p99": K.r3(K.rank(lag, 0.99)),
        "unreflected_after_grace": sum(1 for x in lag if x == math.inf),
        "sent_202": len(sent),
    }


def backlog(d: Path, stage: str, *, warm: float, duration: float) -> dict[str, Any]:
    path = d / f"backlog-{stage}.jsonl"
    rows = (
        [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    )
    pts = [
        (x["t"], x["unprocessed"])
        for x in rows
        if "unprocessed" in x and warm <= x["t"] <= duration + 5
    ]
    slope = K.ols_slope(pts)
    return {
        "samples": len(pts),
        "last": pts[-1][1] if pts else None,
        "slope": None
        if slope is None
        else {
            "per_s": round(slope[0], 4),
            "ci95": [round(slope[1], 4), round(slope[2], 4)],
        },
        "growing": slope is not None and slope[1] > 0,
    }


def latency_ok(s: dict, crit: dict) -> bool:
    return (
        s["p95_ms"] is not None
        and s["p95_ms"] <= crit["read_p95_ms"]
        and s["p99_ms"] <= crit["read_p99_ms"]
    )


def judge(res: dict, crit: dict) -> dict[str, bool]:
    sc = res["scenarios"]
    err = res["error_rate"]
    c1 = [x for x in crit["c1_scenarios"] if x in sc]
    c6 = [x for x in crit["c6_scenarios"] if x in sc]
    return {
        "C1_read_latency": all(latency_ok(sc[x], crit) for x in c1),
        "C2_error_rate": err is not None and err <= crit["error_rate_max"],
        "C3_reflect_p95": res["reflect_s"]["p95"] is not None
        and res["reflect_s"]["p95"] <= crit["reflect_p95_s"],
        "C4_backlog_slope": res["backlog"]["slope"] is not None
        and not res["backlog"]["growing"],
        "C5_dropped": all(s["dropped_iterations"] == 0 for s in sc.values()),
        "C6_arrival": all(
            sc[x]["arrival_err"] is not None
            and abs(sc[x]["arrival_err"]) <= crit["arrival_tolerance"]
            for x in c6
        ),
    }


def report_md(res: dict, crit: dict) -> str:
    ch = res["checks"]

    def ok(name: str) -> str:
        return "통과" if ch[name] else "실패"

    sc = res["scenarios"]
    lines = [
        (
            f"### {res['stage']}({res['kind']}): {res['duration_s'] / 60:g}분, consumer {res['consumers']}"
            f" → **{'통과' if res['pass'] else '실패'}**"
        ),
        "",
        "| 시나리오 | 목표 rps | 도착 rps(오차) | n | p50 / p95 / p99 ms | 상태 | dropped |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {name} | {s['target_rps']} | {s['arrival_rps']} ({s['arrival_err']}) | {s['n']} | "
        f"{s['p50_ms']} / {s['p95_ms']} / {s['p99_ms']} | {s['status']} | {s['dropped_iterations']} |"
        for name, s in sc.items()
    ]
    rf, bl = res["reflect_s"], res["backlog"]
    lines += [
        "",
        "| 기준 | 결과 | 값 |",
        "| --- | --- | --- |",
        (
            f"| C1 조회 p95/p99 ≤ {crit['read_p95_ms']}/{crit['read_p99_ms']}ms"
            f"({', '.join(x for x in crit['c1_scenarios'] if x in sc)}) | {ok('C1_read_latency')} | 위 표 |"
        ),
        f"| C2 전체 오류율 ≤ {crit['error_rate_max'] * 100:g}% | {ok('C2_error_rate')} | {res['error_rate']} |",
        (
            f"| C3 반영 지연 p95 ≤ {crit['reflect_p95_s']:g}초 | {ok('C3_reflect_p95')} | p50 {rf['p50']} · p95 {rf['p95']} · p99 {rf['p99']}초,"
            f" 유예 뒤 미반영 {rf['unreflected_after_grace']}/{rf['sent_202']} |"
        ),
        f"| C4 미처리 기울기 0 | {ok('C4_backlog_slope')} | {bl['slope']} (표본 {bl['samples']}, 마지막 {bl['last']}) |",
        f"| C5 dropped_iterations 0 | {ok('C5_dropped')} | 모든 시나리오 |",
        (
            f"| C6 도착률 ±{crit['arrival_tolerance'] * 100:g}%"
            f"({', '.join(x for x in crit['c6_scenarios'] if x in sc)}) | {ok('C6_arrival')} | 위 표 |"
        ),
        "",
        "`heavy`·`probe`는 판정하지 않는다(보고만).",
        "",
    ]
    return "\n".join(lines)


def cmd_report_b(a: argparse.Namespace) -> None:
    d = Path(a.dir)
    plan = load_plan(a.plan)
    crit = plan["criteria"]
    kind = plan["order"][a.round - 1]
    stage = f"mixed-r{a.round}"
    reqs, failed, dropped = K.read_k6_csv(d / f"{stage}.csv.gz")
    window = max(1e-9, a.duration - a.warm)
    scenarios = {
        sc: scenario_stats(
            reqs, sc, target=k6_rate(rps), window=window, dropped=dropped
        )
        for sc, rps in plan["rounds"][kind].items()
    }
    fails = [v for _, p, v in failed if p == "main"]
    err_rate = sum(fails) / len(fails) if fails else None
    res: dict[str, Any] = {
        "stage": f"b-r{a.round}",
        "round": a.round,
        "kind": kind,
        "duration_s": a.duration,
        "warm_s": a.warm,
        "consumers": a.consumers,
        "scenarios": scenarios,
        "error_rate": None if err_rate is None else round(err_rate, 5),
        "reflect_s": reflect(d, stage, reqs=reqs),
        "backlog": backlog(d, stage, warm=a.warm, duration=a.duration),
    }
    res["checks"] = judge(res, crit)
    res["pass"] = all(res["checks"].values())
    (d / f"mixed-b-r{a.round}.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False)
    )
    md = report_md(res, crit)
    (d / f"mixed-b-r{a.round}.md").write_text(md)
    print(md)


# --------------------------------------------------------------------------- summary-b


def bucket_of(segment_count: int) -> str | None:
    if segment_count < SMALL_LIMIT:
        return "small"
    if str(segment_count) in BUCKETS:
        return str(segment_count)
    return None


def read_logs(log_dir: Path) -> list[dict]:
    """api-logs/r<n>-*.jsonl의 `analytics read` 줄. 각 줄에 회차 번호(_round)를 붙인다."""
    rows = []
    for path in sorted(log_dir.glob("r*-*.jsonl")) if log_dir.exists() else []:
        rnd = int(path.name[1 : path.name.index("-")])
        for line in path.read_text(errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("event") == "analytics read":
                rows.append({**rec, "_round": rnd})
    return rows


def segment_buckets(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for b in BUCKETS:
        sel = [
            r
            for r in rows
            if r.get("operation") == "analytics_daily_stats"
            and r.get("source") == "computed"
            and bucket_of(int(r.get("segment_count", 0))) == b
        ]
        item: dict[str, Any] = {"n": len(sel)}
        for f in MS_FIELDS:
            vals = [float(r[f]) for r in sel if f in r]
            item[f] = {
                "p50": K.r1(K.rank(vals, 0.5)),
                "p95": K.r1(K.rank(vals, 0.95)),
            }
        out[b] = item
    return out


def g1_verdict(rounds: list[dict], plan: dict) -> str:
    by_kind: dict[str, list[bool]] = {}
    for r in rounds:
        by_kind.setdefault(r["kind"], []).append(r["pass"])
    r2, r3 = by_kind.get("R2", []), by_kind.get("R3", [])
    expected = Counter(plan["order"])
    if len(r2) < expected["R2"] or len(r3) < expected["R3"]:
        return "undecidable"
    if all(r3):
        return "no_split_now"
    if all(r2):
        return "split_needed"
    return "undecidable"


def share_table(plan: dict, buckets: dict) -> list[dict]:
    p95 = buckets["10000"]["compute_ms"]["p95"]
    rows = []
    for share in plan["heavy_share_table"]:
        rps = 1000 * share / 60
        rows.append(
            {
                "heavy_share": share,
                "heavy_rps": round(rps, 3),
                "blocked_fraction": None
                if p95 is None
                else round(rps * p95 / 1000 / plan["api_pods"], 5),
            }
        )
    return rows


def read_lines(path: Path, prefix: str) -> list[str]:
    if not path.exists():
        return []
    return [x for x in path.read_text().splitlines() if x.startswith(prefix)]


def summary_md(out: dict) -> str:
    lines = [
        "## 측정 B 요약",
        "",
        f"- G1 결론: **{out['g1']}**",
        "",
        "| 회차 | 종류 | 결과 | 실패 기준 |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r['round']} | {r['kind']} | {'통과' if r['pass'] else '실패'} | "
        f"{', '.join(k for k, v in r['checks'].items() if not v) or '-'} |"
        for r in out["rounds"]
    ]
    lines += [
        "",
        "일간 통계 조회(계산 경로)의 구간 수별 시간(전체 회차, 회차별은 summary-b.json `segment_buckets_by_round`):",
        "",
        "| 구간 수 | n | 읽기 p50/p95 | 변환 p50/p95 | 계산 p50/p95 (ms) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for b, it in out["segment_buckets"].items():
        cells = " | ".join(f"{it[f]['p50']} / {it[f]['p95']}" for f in MS_FIELDS)
        lines.append(f"| {b} | {it['n']} | {cells} |")
    lines += [
        "",
        "| 무거운 사용자 비율 | rps | 걸리는 요청 비율 |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| {x['heavy_share']} | {x['heavy_rps']} | {x['blocked_fraction']} |"
        for x in out["heavy_share_table"]
    ]
    lines += ["", f"원본 DB 복사: {out['base_copy_s']}초"]
    lines += ["", "batch:", *[f"- {x}" for x in out["batch"]]]
    lines += ["", "되돌리기:", *[f"- {x}" for x in out["resets"]], ""]
    return "\n".join(lines)


def cmd_summary_b(a: argparse.Namespace) -> None:
    d, results = Path(a.dir), Path(a.results)
    plan = load_plan(a.plan)
    rounds = []
    for n in range(1, len(plan["order"]) + 1):
        p = d / f"mixed-b-r{n}.json"
        if p.exists():
            rounds.append(json.loads(p.read_text()))
    logs = read_logs(d / "api-logs")
    buckets = segment_buckets(logs)
    base = read_lines(results / "b-base.txt", "base_copy_s=")
    out = {
        "g1": g1_verdict(rounds, plan),
        "rounds": [
            {k: r[k] for k in ("round", "kind", "pass", "checks")} for r in rounds
        ],
        "segment_buckets": buckets,
        "segment_buckets_by_round": {
            str(n): segment_buckets([r for r in logs if r["_round"] == n])
            for n in range(1, len(plan["order"]) + 1)
        },
        "base_copy_s": int(base[-1].split("=", 1)[1]) if base else None,
        "heavy_share_table": share_table(plan, buckets),
        "batch": read_lines(results / "b-batch.txt", "batch"),
        "resets": read_lines(results / "b-reset.txt", "reset"),
    }
    (d / "summary-b.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    md = summary_md(out)
    (d / "summary-b.md").write_text(md)
    print(md)


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("tokens-b")
    s.add_argument("--plan", required=True)
    s.add_argument("--inventory", required=True)
    s.add_argument("--dense", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--exclude", default="")
    s = sub.add_parser("batch-loop")
    s.add_argument("--round", type=int, required=True)
    s.add_argument("--job", required=True)
    s.add_argument("--interval", type=float, required=True)
    s.add_argument("--stop-file", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--log-dir", required=True)
    s = sub.add_parser("report-b")
    s.add_argument("--round", type=int, required=True)
    s.add_argument("--plan", required=True)
    s.add_argument("--dir", required=True)
    s.add_argument("--duration", type=float, required=True)
    s.add_argument("--warm", type=float, required=True)
    s.add_argument("--consumers", default="?")
    s = sub.add_parser("summary-b")
    s.add_argument("--plan", required=True)
    s.add_argument("--dir", required=True)
    s.add_argument("--results", required=True)
    return p


def main() -> None:
    a = build_parser().parse_args()
    if a.cmd == "tokens-b":
        cmd_tokens_b(a)
    elif a.cmd == "batch-loop":
        asyncio.run(cmd_batch_loop(a))
    elif a.cmd == "report-b":
        cmd_report_b(a)
    else:
        cmd_summary_b(a)


if __name__ == "__main__":
    main()

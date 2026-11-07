"""mosemo 에이전트 일일 보고서를 만든다.

보고 구간을 정하고, 새 세션이 적으면 건너뛰고, 아니면 `claude -p`로 판단 부분을 받아
`aggregate.py`의 집계 표와 함께 보고서를 조립한 뒤 macOS 알림을 보낸다.
판정·조립은 입력값을 받는 함수(`run`과 그 아래)로 두고, git·외부 명령은
`main`·`run_analysis`·`send_notification`에만 둔다.
"""

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import aggregate

MODEL = "claude-opus-5-5"
READ_TOOLS = "Read,Grep,Glob"
MIN_NEW_SESSIONS = 5
FIRST_WINDOW = timedelta(days=7)
HEADINGS = ("## 1. 결과", "## 2. 최근 며칠의 양상", "## 3. 개선해야 할 점")
SUCCESS_TITLE = "mosemo 보고서"
FAILURE_TITLE = "mosemo 보고서 실패"
METRICS_FILE = "mosemo-agent-metrics.jsonl"
WORKTREES = ".herdr/worktrees/mosemo-server"
SKILL = Path(__file__).resolve().parents[1] / "skills/agent-daily-report/SKILL.md"
CHECKOUT = Path(__file__).resolve().parents[2]


class Config(NamedTuple):
    reports: Path
    home: Path
    metrics_log: Path
    main_checkout: Path
    worktrees_root: Path
    skill: Path = SKILL


class Paths(NamedTuple):
    """보고서 폴더 아래 실행기가 쓰는 경로(보고서 파일 제외)."""

    reports: Path
    logs: Path
    work: Path
    sessions: Path
    aggregate: Path
    state: Path
    runs: Path


class Analysis(NamedTuple):
    returncode: int
    stdout: str


class Analyze(Protocol):
    def __call__(self, args: Sequence[str], *, stdin: str, cwd: Path) -> Analysis: ...


class Notify(Protocol):
    def __call__(self, title: str, body: str) -> None: ...


class RunError(Exception):
    """보고서를 만들지 못한 실행. 메시지는 한 줄 이유다."""


# 실패로 기록하고 알릴 예외: 파일·명령 실패, 읽을 수 없는 상태·집계, 분석 결과 오류.
# BLE001(`except Exception` 금지)을 지키려고 종류를 나열한다.
FAILURES = (
    RunError,
    OSError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    RuntimeError,
    subprocess.SubprocessError,
)


# 경로와 정책


def report_paths(reports: Path) -> Paths:
    work = reports / ".work"
    return Paths(
        reports=reports,
        logs=reports / ".logs",
        work=work,
        sessions=work / "sessions",
        aggregate=work / "aggregate.json",
        state=reports / ".state.json",
        runs=reports / ".runs.jsonl",
    )


def report_file(reports: Path, now: datetime, *, exists: bool) -> Path:
    """Asia/Seoul 실행 날짜 `YYYY-MM-DD.md`. 이미 있으면 `-HHMMSS`를 붙인다."""
    local = now.astimezone(aggregate.SEOUL)
    suffix = local.strftime("-%H%M%S") if exists else ""
    return reports / f"{local.date().isoformat()}{suffix}.md"


def write_targets(reports: Path, now: datetime) -> list[Path]:
    """실행기가 쓸 수 있는 모든 경로."""
    return [
        *report_paths(reports),
        report_file(reports, now, exists=False),
        report_file(reports, now, exists=True),
    ]


def is_outside(reports: Path, repository: Path) -> bool:
    """보고서 폴더가 저장소 본체 밖인가(경로 구성요소 기준)."""
    return not reports.resolve().is_relative_to(repository.resolve())


def copy_name(path: str) -> str:
    """세션 기록 복사본 이름: 원래 경로의 구성요소를 `_`로 잇는다."""
    return "_".join(part for part in Path(path).parts if part != Path(path).anchor)


def analysis_command() -> list[str]:
    return [
        "claude",
        "-p",
        "--model",
        MODEL,
        "--output-format",
        "json",
        "--tools",
        READ_TOOLS,
        "--allowedTools",
        READ_TOOLS,
        "--strict-mcp-config",
        "--no-session-persistence",
    ]


# 상태와 실행 기록


def read_boundary(state: Path) -> datetime | None:
    if not state.is_file():
        return None
    boundary = json.loads(state.read_text(encoding="utf-8")).get("boundary")
    if not isinstance(boundary, str):
        return None
    parsed = datetime.fromisoformat(boundary)
    if parsed.tzinfo is None:
        message = f".state.json boundary에 시간대 없음: {boundary}"
        raise RunError(message)
    return parsed.astimezone(UTC)


def append_run(
    runs: Path, *, now: datetime, window: dict[str, Any], **fields: Any
) -> None:
    line = {
        "at": aggregate.format_at(now),
        "status": fields["status"],
        "start": window.get("start"),
        "end": window.get("end"),
        "sessions": fields.get("sessions"),
        "cost_usd": fields.get("cost_usd"),
        "report": fields.get("report"),
        "error": fields.get("error"),
    }
    with runs.open("a", encoding="utf-8") as file:
        file.write(json.dumps(line, ensure_ascii=False) + "\n")


def one_line(error: BaseException) -> str:
    text = " ".join(str(error).split())
    return text or type(error).__name__


# 분석 입력과 결과


def prompt(
    skill: str, *, paths: Paths, result: dict[str, Any], copies: dict[str, str]
) -> str:
    window = result["window"]
    unreadable = result["interval"]["panel_unreadable_sessions"]
    sessions = [
        f"  - {paths.sessions / name} ← {original}" for name, original in copies.items()
    ]
    lines = [
        skill.rstrip(),
        "",
        "## 작업 지시",
        "",
        f"- 집계 JSON: {paths.aggregate}",
        f"- 구간: ({window['start']}, {window['end']}]",
        "- 세션 기록 복사본(복사본 ← 원래 경로):",
        *(sessions or ["  - 없음"]),
        f"- 패널 리뷰 unreadable 세션: {', '.join(unreadable) or '없음'}",
    ]
    return "\n".join(lines) + "\n"


def split_sections(text: str) -> list[str]:
    """세 절 제목이 이 순서로 한 줄씩 있으면 각 절 본문, 아니면 `RunError`."""
    lines = text.splitlines()
    positions: list[int] = []
    for heading in HEADINGS:
        start = positions[-1] + 1 if positions else 0
        found = next(
            (i for i in range(start, len(lines)) if lines[i].strip() == heading), None
        )
        if found is None:
            message = f"분석 결과에 절 제목 없음: {heading}"
            raise RunError(message)
        positions.append(found)
    ends = [*positions[1:], len(lines)]
    return [
        "\n".join(lines[begin + 1 : end]).strip()
        for begin, end in zip(positions, ends, strict=True)
    ]


def parse_analysis(analysis: Analysis) -> tuple[list[str], Any]:
    """분석 명령 결과에서 세 절 본문과 `total_cost_usd`."""
    if analysis.returncode != 0:
        message = f"분석 명령 종료 코드 {analysis.returncode}"
        raise RunError(message)
    try:
        output = json.loads(analysis.stdout)
    except ValueError as error:
        message = "분석 명령 출력이 JSON이 아님"
        raise RunError(message) from error
    if not isinstance(output, dict):
        message = "분석 명령 출력이 JSON 객체가 아님"
        raise RunError(message)
    if output.get("is_error"):
        detail = " ".join(str(output.get("result")).split())
        message = f"분석 결과 is_error: {detail}"
        raise RunError(message)
    result = output.get("result")
    if not isinstance(result, str):
        message = "분석 결과에 result 없음"
        raise RunError(message)
    return split_sections(result), output.get("total_cost_usd")


# 보고서 조립


def cell(value: Any) -> str:
    return "-" if value is None else str(value)


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return "없음"
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + " --- |" * len(headers),
        *("| " + " | ".join(cell(value) for value in row) + " |" for row in rows),
    ]
    return "\n".join(lines)


def rule_rows(rules: dict[str, dict[str, Any]]) -> list[list[Any]]:
    edit, stop = rules["edit"], rules["stop"]
    names = sorted(edit.keys() | stop.keys())
    empty = {"count": 0, "sessions": 0}
    return [
        [
            name,
            edit.get(name, empty)["count"],
            edit.get(name, empty)["sessions"],
            stop.get(name, empty)["count"],
            stop.get(name, empty)["sessions"],
        ]
        for name in names
    ]


def interval_tables(interval: dict[str, Any]) -> str:
    summary = table(
        ["세션", "모델 변경 세션", "차단", "capped", "편집 통과율"],
        [
            [
                interval["sessions"],
                interval["model_changed_sessions"],
                interval["blocks"],
                interval["capped"],
                interval["edit"]["pass_rate"],
            ]
        ],
    )
    agents = table(
        ["에이전트/모델", "세션"], list(interval["sessions_by_agent_model"].items())
    )
    stops = table(["Stop 결과", "줄 수"], list(interval["stop_results"].items()))
    rules = table(
        ["규칙", "편집 건수", "편집 세션", "Stop 건수", "Stop 세션"],
        rule_rows(interval["rules"]),
    )
    reviews = table(
        ["리뷰어", "출처", "보고", "형식 준수", "high", "medium", "low"],
        [
            [
                review["reviewer"],
                review["source"],
                review["reports"],
                review["format_ok"],
                *(review["findings"][s] for s in aggregate.SEVERITIES),
            ]
            for review in interval["reviews"]
        ],
    )
    return "\n\n".join(
        [
            f"### 요약\n\n{summary}",
            f"### 에이전트·모델별 세션\n\n{agents}",
            f"### Stop 결과 분포\n\n{stops}",
            f"### 규칙별 위반\n\n{rules}",
            f"### 리뷰어별 보고\n\n{reviews}",
        ]
    )


def rule_total(rules: dict[str, dict[str, Any]], event: str) -> int:
    return sum(rule["count"] for rule in rules[event].values())


def trend_tables(trend: dict[str, Any]) -> str:
    days = table(
        ["날짜", "세션", "차단", "capped", "편집 통과율", "편집 위반", "Stop 위반"],
        [
            [
                day["date"],
                day["sessions"],
                day["blocks"],
                day["capped"],
                day["edit"]["pass_rate"],
                rule_total(day["rules"], "edit"),
                rule_total(day["rules"], "stop"),
            ]
            for day in trend["days"]
        ],
    )
    changes = "\n".join(
        f"- {change['at']}: `{change['harness_rev']}`"
        for change in trend["harness_rev_changes"]
    )
    return f"### 7일 추세\n\n{days}\n\n### harness_rev 변경\n\n{changes or '없음'}"


def render_report(
    result: dict[str, Any], bodies: Sequence[str], *, now: datetime, cost: Any
) -> str:
    window = result["window"]
    local_date = now.astimezone(aggregate.SEOUL).date().isoformat()
    parts = [
        f"# mosemo 에이전트 일일 보고서 {local_date}",
        HEADINGS[0],
        interval_tables(result["interval"]),
        bodies[0],
        HEADINGS[1],
        trend_tables(result["trend"]),
        bodies[1],
        HEADINGS[2],
        bodies[2],
        "## 실행 정보",
        "\n".join(
            [
                f"- 구간: ({window['start']}, {window['end']}]",
                f"- 활동 세션 수: {result['interval']['sessions']}",
                f"- 분석 모델: `{MODEL}`",
                f"- 비용: {cell(cost)} USD",
            ]
        ),
    ]
    return "\n\n".join(part for part in parts if part) + "\n"


# 실행 흐름


def collect(config: Config, *, start: datetime, now: datetime) -> dict[str, Any]:
    roots = [config.main_checkout, config.worktrees_root]
    transcripts = aggregate.read_transcripts(config.home, start=start, roots=roots)
    log = config.metrics_log.read_text(encoding="utf-8", errors="replace")
    return aggregate.aggregate(log.splitlines(), transcripts, start=start, end=now)


def prepare_work(paths: Paths, result: dict[str, Any]) -> dict[str, str]:
    """`.work/`를 다시 만들고 집계 JSON과 세션 기록 복사본을 둔다. 복사본 이름 → 원래 경로."""
    shutil.rmtree(paths.work, ignore_errors=True)
    paths.sessions.mkdir(parents=True)
    paths.aggregate.write_text(
        json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    copies = {copy_name(path): path for path in result["interval"]["session_files"]}
    for name, original in copies.items():
        shutil.copyfile(original, paths.sessions / name)
    return copies


def publish(
    config: Config, result: dict[str, Any], *, now: datetime, analyze: Analyze
) -> tuple[Path, Any]:
    """분석을 받아 보고서를 쓰고 경계를 옮긴다. 보고서 경로와 비용."""
    paths = report_paths(config.reports)
    copies = prepare_work(paths, result)
    stdin = prompt(
        config.skill.read_text(encoding="utf-8"),
        paths=paths,
        result=result,
        copies=copies,
    )
    bodies, cost = parse_analysis(
        analyze(analysis_command(), stdin=stdin, cwd=paths.work)
    )
    report = report_file(
        config.reports,
        now,
        exists=report_file(config.reports, now, exists=False).exists(),
    )
    report.write_text(
        render_report(result, bodies, now=now, cost=cost), encoding="utf-8"
    )
    try:
        paths.state.write_text(
            json.dumps({"boundary": aggregate.format_at(now)}) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # 경계를 옮기지 못한 실행은 실패이므로 보고서도 남기지 않는다.
        report.unlink(missing_ok=True)
        raise
    return report, cost


def run(config: Config, *, now: datetime, analyze: Analyze, notify: Notify) -> int:
    paths = report_paths(config.reports)
    if not is_outside(config.reports, config.main_checkout):
        notify(FAILURE_TITLE, f"보고서 폴더가 저장소 안에 있음 · {paths.runs}")
        return 1
    # `.runs.jsonl`의 `start`·`end`·`sessions`. 알게 되는 대로 채운다.
    window: dict[str, Any] = {"end": aggregate.format_at(now)}
    try:
        for folder in (paths.reports, paths.logs, paths.work):
            folder.mkdir(parents=True, exist_ok=True)
        start = read_boundary(paths.state) or now - FIRST_WINDOW
        window["start"] = aggregate.format_at(start)
        result = collect(config, start=start, now=now)
        sessions = window["sessions"] = result["interval"]["sessions"]
        if result["interval"]["new_sessions"] < MIN_NEW_SESSIONS:
            append_run(
                paths.runs, now=now, window=window, status="skipped", sessions=sessions
            )
            return 0
        previous = paths.state.read_bytes() if paths.state.is_file() else None
        report, cost = publish(config, result, now=now, analyze=analyze)
        try:
            append_run(
                paths.runs,
                now=now,
                window=window,
                status="report",
                sessions=sessions,
                cost_usd=cost,
                report=str(report),
            )
        except OSError:
            # 실행 기록을 남기지 못한 실행은 실패이므로 보고서와 경계를 되돌린다.
            report.unlink(missing_ok=True)
            if previous is None:
                paths.state.unlink(missing_ok=True)
            else:
                paths.state.write_bytes(previous)
            raise
    except FAILURES as error:
        return fail(paths, error, now=now, window=window, notify=notify)
    notify(SUCCESS_TITLE, str(report))
    return 0


def fail(
    paths: Paths,
    error: BaseException,
    *,
    now: datetime,
    window: dict[str, Any],
    notify: Notify,
) -> int:
    """`.runs.jsonl`에 `failed`를 남기고(쓸 수 없으면 건너뛴다) 실패 알림을 보낸다."""
    reason = one_line(error)
    # 보고서 폴더를 만들지 못한 실패도 알림은 보낸다.
    with contextlib.suppress(OSError):
        paths.reports.mkdir(parents=True, exist_ok=True)
        append_run(
            paths.runs,
            now=now,
            window=window,
            status="failed",
            sessions=window.get("sessions"),
            error=reason,
        )
    notify(FAILURE_TITLE, f"{reason} · {paths.runs}")
    return 1


def build_config(
    reports: Path, *, common: Path, home: Path, metrics: str | None
) -> Config:
    """공통 git 디렉터리와 홈에서 실행 설정. 지표 로그는 `metrics`가 있으면 그 경로."""
    return Config(
        reports=reports,
        home=home,
        metrics_log=Path(metrics) if metrics else common / METRICS_FILE,
        main_checkout=common.parent,
        worktrees_root=home / WORKTREES,
    )


# 외부 명령 연결


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def send_notification(title: str, body: str) -> None:
    script = (
        f"display notification {applescript_string(body)} "
        f"with title {applescript_string(title)}"
    )
    subprocess.run(["osascript", "-e", script], check=False)


def run_analysis(args: Sequence[str], *, stdin: str, cwd: Path) -> Analysis:
    completed = subprocess.run(
        list(args), input=stdin, cwd=cwd, capture_output=True, text=True, check=False
    )
    return Analysis(completed.returncode, completed.stdout)


def git_common_dir(directory: Path) -> Path:
    output = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return Path(output.strip())


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, default=Path("~/mosemo-reports"))
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    now: datetime | None = None,
    common_dir: Callable[[Path], Path] = git_common_dir,
    analyze: Analyze = run_analysis,
    notify: Notify = send_notification,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    now = now or datetime(*time.gmtime()[:6], tzinfo=UTC)
    try:
        reports = args.reports_dir.expanduser()
    except FAILURES as error:
        # 홈을 알 수 없으면 보고서 폴더도 모르므로 알림만 보낸다.
        notify(FAILURE_TITLE, one_line(error))
        return 1
    try:
        common = common_dir(Path(__file__).resolve().parent)
        home = Path.home()
    except FAILURES as error:
        # 저장소 본체를 모르므로 이 실행기 파일의 checkout 안이면 아무것도 쓰지 않는다.
        if not is_outside(reports, CHECKOUT):
            notify(FAILURE_TITLE, f"{one_line(error)} · {report_paths(reports).runs}")
            return 1
        window = {"start": None, "end": aggregate.format_at(now)}
        return fail(report_paths(reports), error, now=now, window=window, notify=notify)
    config = build_config(
        reports, common=common, home=home, metrics=os.environ.get("MOSEMO_METRICS_LOG")
    )
    return run(config, now=now, analyze=analyze, notify=notify)


if __name__ == "__main__":
    raise SystemExit(main())

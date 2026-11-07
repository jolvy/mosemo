"""일일 보고서의 숫자를 지표 로그와 세션 기록에서 결정적으로 집계한다.

같은 입력이면 같은 출력이 나온다. 현재 시각은 CLI에서 `--end`가 없을 때만 읽는다.
git과 subprocess를 쓰지 않는다. 계산은 `aggregate`(파일 읽기와 분리한 순수 함수)가 한다.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SEOUL = ZoneInfo("Asia/Seoul")
TREND_DAYS = 7
SESSION_EVENTS = ("edit", "stop")
REVIEW_AGENTS = frozenset({"code-reviewer", "domain-reviewer", "security-auditor"})
SEVERITIES = ("high", "medium", "low")
REVIEW_LINE = re.compile(r"^REVIEW high=(\d+) medium=(\d+) low=(\d+)$")
# Issue #227의 경로 정규식에 키(리뷰 폴더·파일 이름 줄기)를 꺼낼 그룹만 더했다.
PANEL_PATH = re.compile(
    r"(?:~|/[^\s'\"]*)/\.herdr/worktrees/mosemo-server/"
    r"([^/\s'\"]+-review)/([^/\s'\"]+)\.md"
)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
REDIRECT_TARGET = re.compile(r">\s*(['\"]?)([^\s'\"<>|;&]+)\1")
TEE_BEFORE = re.compile(r"\btee(?:\s+-\S+)*\s+['\"]?$")
# 경로 바로 앞뒤의 Python 쓰기: `Path('P')….write_text(`, `open('P', "w")`·`open(Path('P'), mode="a")`.
WRITE_TEXT_AFTER = re.compile(r"^['\"]?\)?(?:\.\w+\(\))*\.write_text\(")
OPEN_BEFORE = re.compile(r"\bopen\(\s*(?:Path\(\s*)?['\"]?$")
OPEN_AFTER = re.compile(r"^['\"]?\)?(?:\.\w+\(\))*\s*,\s*(?:mode\s*=\s*)?['\"][wax]")
ADD_FILE = "*** Add File: "


class Transcript(NamedTuple):
    """FR-6a로 고른 세션 기록 한 파일. `kind`는 `claude` 또는 `codex`다."""

    path: str
    kind: str
    lines: list[str]


class Record(NamedTuple):
    at: datetime
    order: int
    line: dict[str, Any]


class PanelWrite(NamedTuple):
    at: datetime
    key: tuple[str, str]
    body: str | None


# 지표 로그 읽기


def parse_at(value: Any) -> datetime | None:
    """지표 로그의 `at`(UTC `%Y-%m-%dT%H:%M:%SZ`). 읽을 수 없으면 `None`."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def format_at(value: datetime) -> str:
    return value.astimezone(UTC).strftime(AT_FORMAT)


def normalize_line(line: dict[str, Any]) -> dict[str, Any]:
    """기존 줄 읽기: `event` 없음 → `stop`, `branch` 없음 → `null`, 문자열 `harness_dirty`."""
    normalized = {"event": "stop", "branch": None, **line}
    dirty = normalized.get("harness_dirty")
    if isinstance(dirty, str):
        normalized["harness_dirty"] = {"true": True, "false": False}.get(dirty)
    return normalized


def read_log(lines: Iterable[str]) -> tuple[list[Record], int]:
    """JSONL 줄을 정규화한 기록과 건너뛴 줄 수로 나눈다. 빈 줄은 세지 않는다."""
    records: list[Record] = []
    skipped = 0
    for order, text in enumerate(lines):
        if not text.strip():
            continue
        try:
            line = json.loads(text)
        except ValueError:
            skipped += 1
            continue
        at = parse_at(line.get("at")) if isinstance(line, dict) else None
        if at is None:
            skipped += 1
            continue
        records.append(Record(at, order, normalize_line(line)))
    return records, skipped


# 지표 줄 집계


def text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def agent_model_key(agent: Any, model: Any) -> str:
    return f"{text_or_none(agent) or 'null'}/{text_or_none(model) or 'null'}"


def rule_items(line: dict[str, Any]) -> Iterator[tuple[str, int]]:
    rules = line.get("rules")
    if not isinstance(rules, dict):
        return
    for rule, count in rules.items():
        if isinstance(count, int) and not isinstance(count, bool):
            yield str(rule), count


def edit_stats(lines: Sequence[dict[str, Any]]) -> dict[str, Any]:
    edits = [line for line in lines if line["event"] == "edit"]
    clean = sum(1 for line in edits if line.get("rules") == {})
    rate = round(clean / len(edits), 4) if edits else None
    return {"lines": len(edits), "clean_lines": clean, "pass_rate": rate}


def stop_stats(lines: Sequence[dict[str, Any]]) -> dict[str, Any]:
    results = Counter(
        text_or_none(line.get("result")) or "null"
        for line in lines
        if line["event"] == "stop"
    )
    return {
        "stop_results": dict(sorted(results.items())),
        "blocks": results["block"] + results["error"],
        "capped": results["capped"],
    }


def rule_counts(lines: Sequence[dict[str, Any]], event: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for line in lines:
        if line["event"] == event:
            counts.update(dict(rule_items(line)))
    return dict(sorted(counts.items()))


def rule_stats(lines: Sequence[dict[str, Any]], event: str) -> dict[str, Any]:
    sessions: defaultdict[str, set[str]] = defaultdict(set)
    for line in lines:
        session = text_or_none(line.get("session"))
        if line["event"] == event and session:
            for rule, _ in rule_items(line):
                sessions[rule].add(session)
    return {
        rule: {"count": count, "sessions": len(sessions[rule])}
        for rule, count in rule_counts(lines, event).items()
    }


def by_agent_model(lines: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """줄 자체의 `agent`·`model`로 나눈 Stop 결과·차단·편집 통과율·규칙 합계."""
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        groups[agent_model_key(line.get("agent"), line.get("model"))].append(line)
    return {
        key: {
            **stop_stats(group),
            "edit": edit_stats(group),
            "rules": {event: rule_counts(group, event) for event in SESSION_EVENTS},
        }
        for key, group in sorted(groups.items())
    }


def session_lines(records: Iterable[Record]) -> dict[str, list[dict[str, Any]]]:
    """세션을 만드는 줄(`edit`·`stop`, 비어 있지 않은 `session`)을 세션별 시각순으로."""
    sessions: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in sorted(records, key=lambda record: (record.at, record.order)):
        session = text_or_none(record.line.get("session"))
        if record.line["event"] in SESSION_EVENTS and session:
            sessions[session].append(record.line)
    return sessions


def session_stats(records: Sequence[Record]) -> dict[str, Any]:
    sessions = session_lines(records)
    by_key: Counter[str] = Counter()
    changed = 0
    for lines in sessions.values():
        models = [model for line in lines if (model := text_or_none(line.get("model")))]
        by_key[
            agent_model_key(lines[-1].get("agent"), models[-1] if models else None)
        ] += 1
        changed += len(set(models)) > 1
    return {
        "sessions": len(sessions),
        "sessions_by_agent_model": dict(sorted(by_key.items())),
        "model_changed_sessions": changed,
    }


def line_stats(records: Sequence[Record]) -> dict[str, Any]:
    """구간과 7일 추세의 날짜마다 같은 방식으로 내는 숫자."""
    lines = [
        record.line for record in records if record.line["event"] in SESSION_EVENTS
    ]
    return {
        **session_stats(records),
        **stop_stats(lines),
        "by_agent_model": by_agent_model(lines),
        "edit": edit_stats(lines),
        "rules": {event: rule_stats(lines, event) for event in SESSION_EVENTS},
    }


def new_sessions(
    records: Sequence[Record], window: Sequence[Record], *, start: datetime | None
) -> int:
    """활동 세션 중 `start` 이전(`at` ≤ `start`)에 `edit`·`stop` 줄이 없던 세션 수."""
    before = session_lines(r for r in records if start is not None and r.at <= start)
    return len(session_lines(window).keys() - before.keys())


# 리뷰 집계


def review_findings(body: str) -> dict[str, int] | None:
    """보고의 첫 비어 있지 않은 줄이 `REVIEW` 형식이면 심각도별 건수, 아니면 `None`."""
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    match = REVIEW_LINE.match(first)
    if match is None:
        return None
    return dict(zip(SEVERITIES, map(int, match.groups()), strict=True))


def log_reviews(window: Sequence[Record]) -> list[dict[str, Any]]:
    """`agent_stop` 리뷰 줄은 같은 세션·리뷰어의 마지막 줄만, `subagent_stop`은 모두."""
    reviews: list[dict[str, Any]] = []
    last_agent_stop: dict[tuple[Any, Any], dict[str, Any]] = {}
    for record in sorted(window, key=lambda record: (record.at, record.order)):
        line = record.line
        if line["event"] != "review":
            continue
        if line.get("via") == "agent_stop":
            last_agent_stop[line.get("session"), line.get("reviewer")] = line
        else:
            reviews.append(line)
    reviews.extend(last_agent_stop.values())
    return [
        {
            "reviewer": text_or_none(line.get("reviewer")) or "null",
            "source": "log",
            "format_ok": line.get("format_ok") is True,
            "findings": line.get("findings"),
        }
        for line in reviews
    ]


def review_stats(reports: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for report in reports:
        group = groups.setdefault(
            (report["reviewer"], report["source"]),
            {"reports": 0, "format_ok": 0, "findings": dict.fromkeys(SEVERITIES, 0)},
        )
        group["reports"] += 1
        findings = report["findings"]
        if not report["format_ok"] or not isinstance(findings, dict):
            continue
        group["format_ok"] += 1
        for severity in SEVERITIES:
            count = findings.get(severity)
            group["findings"][severity] += count if isinstance(count, int) else 0
    return [
        {"reviewer": reviewer, "source": source, **group}
        for (reviewer, source), group in sorted(groups.items())
    ]


# 세션 기록: 선택과 패널 리뷰 추출


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def json_lines(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    for text in lines:
        try:
            entry = json.loads(text)
        except ValueError:
            continue
        if isinstance(entry, dict):
            yield entry


def first_line(lines: Sequence[str]) -> dict[str, Any]:
    return next(json_lines(lines[:1]), {})


def codex_meta(lines: Sequence[str]) -> dict[str, Any]:
    entry = first_line(lines)
    payload = entry.get("payload")
    if entry.get("type") != "session_meta" or not isinstance(payload, dict):
        return {}
    return payload


def transcript_cwd(kind: str, lines: Sequence[str]) -> str | None:
    """Claude Code는 처음 나오는 `cwd`, Codex는 첫 줄 `session_meta`의 `payload.cwd`."""
    if kind == "codex":
        return text_or_none(codex_meta(lines).get("cwd"))
    return next(
        (cwd for entry in json_lines(lines) if (cwd := text_or_none(entry.get("cwd")))),
        None,
    )


def is_selected(cwd: str | None, roots: Sequence[Path]) -> bool:
    """작업 폴더가 `roots` 중 하나와 같거나 그 아래인가(경로 구성요소 기준)."""
    if cwd is None:
        return False
    real = Path(os.path.realpath(Path(cwd).expanduser()))
    return any(real.is_relative_to(os.path.realpath(root)) for root in roots)


def transcript_session(transcript: Transcript) -> str:
    if transcript.kind == "codex":
        meta = codex_meta(transcript.lines)
        return text_or_none(meta.get("id")) or Path(transcript.path).stem
    return Path(transcript.path).stem


def is_review_subagent(transcript: Transcript) -> bool:
    source = codex_meta(transcript.lines).get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    role = subagent.get("agent_role") if isinstance(subagent, dict) else None
    return transcript.kind == "codex" and role in REVIEW_AGENTS


def panel_key(path: str) -> tuple[str, str] | None:
    match = PANEL_PATH.search(path)
    return (match.group(1), match.group(2)) if match else None


def heredoc_writes(command: str) -> Iterator[tuple[tuple[str, str], str]]:
    """`cat > PATH <<'EOF'` 형태의 heredoc 본문(구분자 줄 앞까지)."""
    lines = command.split("\n")
    for index, line in enumerate(lines):
        heredoc = HEREDOC.search(line)
        target = REDIRECT_TARGET.search(line)
        key = panel_key(target.group(2)) if heredoc and target else None
        if heredoc is None or key is None:
            continue
        tag = heredoc.group(2)
        rest = lines[index + 1 :]
        end = next((i for i, body in enumerate(rest) if body.strip() == tag), len(rest))
        yield key, "\n".join(rest[:end])


def write_forms(command: str) -> Iterator[tuple[str, str]]:
    """경로에 쓰는 형태(리다이렉트, `tee`, `write_text`, `open(…, "w")`)가 보인 경로."""
    for match in PANEL_PATH.finditer(command):
        before = command[: match.start()]
        after = command[match.end() :]
        if (
            before.rstrip("'\"").rstrip().endswith(">")
            or TEE_BEFORE.search(before)
            or WRITE_TEXT_AFTER.match(after)
            or (OPEN_BEFORE.search(before) and OPEN_AFTER.match(after))
        ):
            yield match.group(1), match.group(2)


def patch_writes(text: str) -> Iterator[tuple[tuple[str, str], str]]:
    """`apply_patch` 본문의 `*** Add File: PATH` 뒤 `+` 줄(앞 `+` 제거)."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        key = panel_key(line[len(ADD_FILE) :]) if line.startswith(ADD_FILE) else None
        if key is None:
            continue
        body = []
        for added in lines[index + 1 :]:
            if not added.startswith("+"):
                break
            body.append(added[1:])
        yield key, "\n".join(body)


def string_values(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from string_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from string_values(child)


def tool_inputs(payload: dict[str, Any]) -> Iterator[str]:
    """Codex 도구 호출 입력 문자열(`custom_tool_call.input`, `function_call.arguments`)."""
    if payload.get("type") == "custom_tool_call":
        yield from string_values(payload.get("input"))
    elif payload.get("type") == "function_call":
        arguments = str(payload.get("arguments", ""))
        try:
            yield from string_values(json.loads(arguments))
        except ValueError:
            yield arguments


def shell_writes(command: str, at: datetime) -> Iterator[PanelWrite]:
    """명령 하나의 쓰기. heredoc으로 본문을 꺼낸 경로는 그 리다이렉트를 따로 세지 않는다."""
    bodies = dict(heredoc_writes(command))
    for key, body in bodies.items():
        yield PanelWrite(at, key, body)
    for key in dict.fromkeys(write_forms(command)):
        if key not in bodies:
            yield PanelWrite(at, key, None)


def claude_writes(entry: dict[str, Any], at: datetime) -> Iterator[PanelWrite]:
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    for block in content if isinstance(content, list) else []:
        tool = block.get("input") if isinstance(block, dict) else None
        if block.get("type") != "tool_use" or not isinstance(tool, dict):
            continue
        if block.get("name") == "Write":
            key = panel_key(str(tool.get("file_path", "")))
            if key is not None:
                yield PanelWrite(at, key, str(tool.get("content", "")))
        elif block.get("name") == "Bash":
            yield from shell_writes(str(tool.get("command", "")), at)


def codex_writes(entry: dict[str, Any], at: datetime) -> Iterator[PanelWrite]:
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return
    item = payload.get("item")
    if (
        entry.get("type") == "event_msg"
        and payload.get("type") == "item_completed"
        and isinstance(item, dict)
        and item.get("type") == "CommandExecution"
    ):
        command = item.get("command")
        last = command[-1] if isinstance(command, list) and command else command
        yield from shell_writes(str(last or ""), at)
    if entry.get("type") == "response_item":
        for text in tool_inputs(payload):
            for key, body in patch_writes(text):
                yield PanelWrite(at, key, body)


def panel_writes(transcript: Transcript) -> Iterator[PanelWrite]:
    """세션 기록 한 파일의 패널 리뷰 쓰기를 기록 순서대로. 본문을 못 꺼내면 `body`=`None`."""
    if is_review_subagent(transcript):
        return
    writes = codex_writes if transcript.kind == "codex" else claude_writes
    for entry in json_lines(transcript.lines):
        at = parse_timestamp(entry.get("timestamp"))
        if at is not None:
            yield from writes(entry, at)


def panel_reviews(
    transcripts: Sequence[Transcript], *, start: datetime | None, end: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    """구간 안 패널 리뷰 보고(같은 세션·경로는 마지막 쓰기)와 본문을 못 꺼낸 세션."""
    reports: list[dict[str, Any]] = []
    unreadable: set[str] = set()
    for transcript in transcripts:
        session = transcript_session(transcript)
        last: dict[tuple[str, str], str | None] = {}
        for write in panel_writes(transcript):
            if in_window(write.at, start=start, end=end):
                last[write.key] = write.body
        if None in last.values():
            unreadable.add(session)
        for (_, stem), body in sorted(last.items()):
            if body is None:
                continue
            findings = review_findings(body)
            reports.append(
                {
                    "reviewer": f"panel:{stem}",
                    "source": "panel",
                    "format_ok": findings is not None,
                    "findings": findings,
                }
            )
    return reports, sorted(unreadable)


def session_files(
    transcripts: Sequence[Transcript], *, start: datetime | None, end: datetime
) -> list[str]:
    """줄의 `timestamp`가 구간 안인 줄이 하나라도 있는 세션 기록 경로."""
    return sorted(
        transcript.path
        for transcript in transcripts
        if any(
            (at := parse_timestamp(entry.get("timestamp"))) is not None
            and in_window(at, start=start, end=end)
            for entry in json_lines(transcript.lines)
        )
    )


# 시간과 전체 집계


def in_window(at: datetime, *, start: datetime | None, end: datetime) -> bool:
    """구간 `(start, end]`. `start`가 없으면 하한이 없다."""
    return (start is None or at > start) and at <= end


def trend_days(end: datetime) -> list[date]:
    last = end.astimezone(SEOUL).date()
    return [last - timedelta(days=offset) for offset in range(TREND_DAYS - 1, -1, -1)]


def harness_rev_changes(records: Sequence[Record]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    previous = None
    for record in sorted(records, key=lambda record: (record.at, record.order)):
        rev = record.line.get("harness_rev")
        if rev is not None and rev != previous:
            changes.append({"at": format_at(record.at), "harness_rev": str(rev)})
            previous = rev
    return changes


def trend(records: Sequence[Record], end: datetime) -> dict[str, Any]:
    """`end`의 Asia/Seoul 날짜 D를 포함한 D-6~D의 일별 값(`end` 이후 줄은 제외)."""
    days = trend_days(end)
    daily: dict[date, list[Record]] = {day: [] for day in days}
    for record in records:
        day = record.at.astimezone(SEOUL).date()
        if day in daily and record.at <= end:
            daily[day].append(record)
    return {
        "days": [{"date": day.isoformat(), **line_stats(daily[day])} for day in days],
        "harness_rev_changes": harness_rev_changes(
            [record for day in days for record in daily[day]]
        ),
    }


def aggregate(
    log_lines: Iterable[str],
    transcripts: Sequence[Transcript],
    *,
    start: datetime | None,
    end: datetime,
) -> dict[str, Any]:
    """지표 로그 줄과 FR-6a로 고른 세션 기록에서 보고서 숫자를 만든다."""
    records, skipped = read_log(log_lines)
    window = [
        record for record in records if in_window(record.at, start=start, end=end)
    ]
    panel, unreadable = panel_reviews(transcripts, start=start, end=end)
    return {
        "window": {"start": format_at(start) if start else None, "end": format_at(end)},
        "skipped_lines": skipped,
        "interval": {
            **line_stats(window),
            "new_sessions": new_sessions(records, window, start=start),
            "reviews": review_stats([*log_reviews(window), *panel]),
            "panel_unreadable_sessions": unreadable,
            "session_files": session_files(transcripts, start=start, end=end),
        },
        "trend": trend(records, end),
    }


# 파일 읽기와 CLI


def transcript_paths(home: Path) -> Iterator[tuple[str, Path]]:
    for path in sorted((home / ".claude/projects").glob("*/*.jsonl")):
        yield "claude", path
    for path in sorted((home / ".codex/sessions").glob("*/*/*/*.jsonl")):
        yield "codex", path


def read_transcripts(
    home: Path, *, start: datetime | None, roots: Sequence[Path]
) -> list[Transcript]:
    """`home` 아래 세션 기록 중 작업 폴더가 `roots` 안인 것. 구간 시작 전에 수정된 파일은 읽지 않는다."""
    transcripts = []
    for kind, path in transcript_paths(home):
        try:
            if start is not None and path.stat().st_mtime < start.timestamp():
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if is_selected(transcript_cwd(kind, lines), roots):
            transcripts.append(Transcript(str(path), kind, lines))
    return transcripts


def parse_cli_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        message = f"시간대가 없는 시각: {value}"
        raise argparse.ArgumentTypeError(message)
    return parsed.astimezone(UTC)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--end", type=parse_cli_time)
    parser.add_argument("--start", type=parse_cli_time)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--main-checkout", type=Path, required=True)
    parser.add_argument("--worktrees-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    end = args.end or datetime(*time.gmtime()[:6], tzinfo=UTC)
    roots = [args.main_checkout, args.worktrees_root]
    transcripts = read_transcripts(args.home, start=args.start, roots=roots)
    log_lines = args.log.read_text(encoding="utf-8", errors="replace").splitlines()
    result = aggregate(log_lines, transcripts, start=args.start, end=end)
    sys.stdout.write(
        json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

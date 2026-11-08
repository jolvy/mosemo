import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

START = datetime(2026, 11, 6, 15, tzinfo=UTC)  # 2026-11-07 00:00 Asia/Seoul
END = datetime(2026, 11, 7, 14, tzinfo=UTC)  # 2026-11-07 23:00 Asia/Seoul
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
GPT = "gpt-6.1-sol"
FOUND = {"high": 0, "medium": 1, "low": 0}


def metric(at: str, event: str | None, session: str, **fields: Any) -> str:
    line = {"at": f"2026-11-{at}Z", "session": session, **fields}
    if event is not None:
        line["event"] = event
    return json.dumps(line)


def stop(at: str, session: str, agent: str, model: str | None, **fields: Any) -> str:
    return metric(at, "stop", session, agent=agent, model=model, **fields)


def edit(at: str, session: str, agent: str, model: str, rules: dict) -> str:
    return metric(at, "edit", session, agent=agent, model=model, rules=rules)


def review(at: str, session: str, via: str, findings: dict | None) -> dict:
    return {"at": at, "session": session, "via": via, "findings": findings}


LOG = [
    # `start` 이전부터 이어진 세션 s1의 옛 줄(`event`·`branch` 없음, 문자열 harness_dirty).
    metric(
        "06T10:00:00",
        None,
        "s1",
        agent="claude",
        model=OPUS,
        harness_rev="aaa1111",
        harness_dirty="true",
        result="block",
        session_blocks=1,
        rules={"E501": 1},
    ),
    '{"at": "2026-11-07T01:00:00Z", "event": ',
    "[1, 2]",
    '{"at": "yesterday", "event": "edit", "session": "s1"}',
    "",
    metric(
        "07T01:00:00",
        "edit",
        "s1",
        agent="claude",
        model=OPUS,
        harness_rev="aaa1111",
        harness_dirty="false",
        rules={"E501": 2},
    ),
    edit("07T01:05:00", "s1", "claude", OPUS, {"E501": 1}),
    stop(
        "07T01:10:00",
        "s1",
        "claude",
        SONNET,
        harness_rev="bbb2222",
        result="block",
        session_blocks=5,
        rules={"E501": 1, "F401": 2},
    ),
    stop("07T01:20:00", "s1", "claude", SONNET, result="allow", rules={}),
    metric(
        "07T02:00:00",
        "edit",
        "s2",
        agent="codex",
        model=GPT,
        harness_rev="aaa1111",
        harness_dirty="x",
        rules={},
    ),
    stop("07T02:10:00", "s2", "codex", GPT, result="capped", rules={"E501": 3}),
    stop("07T03:00:00", "s3", "codex", None, result="error", rules={"pytest": 1}),
    edit("07T03:05:00", "s3", "codex", GPT, {}),
    stop("07T04:00:00", "", "claude", OPUS, result="block", rules={"E501": 1}),
    metric(
        "07T05:00:00",
        "review",
        "s9",
        agent="codex",
        reviewer="code-reviewer",
        findings={"high": 1, "medium": 0, "low": 0},
        format_ok=True,
        via="subagent_stop",
    ),
    metric(
        "07T05:10:00",
        "review",
        "s9",
        agent="codex",
        reviewer="code-reviewer",
        findings=None,
        format_ok=False,
        via="subagent_stop",
    ),
    metric(
        "07T06:00:00",
        "review",
        "s4",
        agent="claude",
        reviewer="domain-reviewer",
        findings={"high": 0, "medium": 2, "low": 1},
        format_ok=True,
        via="agent_stop",
    ),
    metric(
        "07T06:30:00",
        "review",
        "s4",
        agent="claude",
        reviewer="domain-reviewer",
        findings=FOUND,
        format_ok=True,
        via="agent_stop",
    ),
    stop("07T20:00:00", "s5", "claude", OPUS, result="allow", rules={}),
    stop("07T06:40:00", "s4", "claude", OPUS, result="allow", branch="feat/x"),
]


def no_roots(report_aggregate: ModuleType) -> Any:
    """세션 기록 없이 부르는 집계의 루트(어느 경로도 쓰이지 않는다)."""
    return report_aggregate.Roots(Path("/main"), Path("/worktrees"))


def rule(count: int, sessions: int) -> dict[str, int]:
    return {"count": count, "sessions": sessions}


def no_edits() -> dict[str, Any]:
    return {"lines": 0, "clean_lines": 0, "pass_rate": None}


BY_AGENT_MODEL = {
    "claude/claude-opus-5-5": {
        "stop_results": {"allow": 1, "block": 1},
        "blocks": 1,
        "capped": 0,
        "edit": {"lines": 2, "clean_lines": 0, "pass_rate": 0.0},
        "rules": {"edit": {"E501": 3}, "stop": {"E501": 1}},
    },
    "claude/claude-sonnet-5-5": {
        "stop_results": {"allow": 1, "block": 1},
        "blocks": 1,
        "capped": 0,
        "edit": no_edits(),
        "rules": {"edit": {}, "stop": {"E501": 1, "F401": 2}},
    },
    "codex/gpt-6.1-sol": {
        "stop_results": {"capped": 1},
        "blocks": 0,
        "capped": 1,
        "edit": {"lines": 2, "clean_lines": 2, "pass_rate": 1.0},
        "rules": {"edit": {}, "stop": {"E501": 3}},
    },
    "codex/null": {
        "stop_results": {"error": 1},
        "blocks": 1,
        "capped": 0,
        "edit": no_edits(),
        "rules": {"edit": {}, "stop": {"pytest": 1}},
    },
}
INTERVAL_STATS = {
    "sessions": 4,
    "sessions_by_agent_model": {
        "claude/claude-opus-5-5": 1,
        "claude/claude-sonnet-5-5": 1,
        "codex/gpt-6.1-sol": 2,
    },
    "model_changed_sessions": 1,
    "stop_results": {"allow": 2, "block": 2, "capped": 1, "error": 1},
    "blocks": 3,
    "capped": 1,
    "by_agent_model": BY_AGENT_MODEL,
    "edit": {"lines": 4, "clean_lines": 2, "pass_rate": 0.5},
    "rules": {
        "edit": {"E501": rule(3, 1)},
        "stop": {"E501": rule(5, 2), "F401": rule(2, 1), "pytest": rule(1, 1)},
    },
}
EMPTY_DAY = {
    "sessions": 0,
    "sessions_by_agent_model": {},
    "model_changed_sessions": 0,
    "stop_results": {},
    "blocks": 0,
    "capped": 0,
    "by_agent_model": {},
    "edit": no_edits(),
    "rules": {"edit": {}, "stop": {}},
}
OLD_LINE_DAY = {
    **EMPTY_DAY,
    "sessions": 1,
    "sessions_by_agent_model": {"claude/claude-opus-5-5": 1},
    "stop_results": {"block": 1},
    "blocks": 1,
    "by_agent_model": {
        "claude/claude-opus-5-5": {
            "stop_results": {"block": 1},
            "blocks": 1,
            "capped": 0,
            "edit": no_edits(),
            "rules": {"edit": {}, "stop": {"E501": 1}},
        }
    },
    "rules": {"edit": {}, "stop": {"E501": rule(1, 1)}},
}


def test_normalize_line_reads_old_lines(report_aggregate: ModuleType) -> None:
    normalize = report_aggregate.normalize_line

    assert normalize({"at": "x", "harness_dirty": "true"}) == {
        "at": "x",
        "event": "stop",
        "branch": None,
        "harness_dirty": True,
    }
    assert normalize({"harness_dirty": "false"})["harness_dirty"] is False
    assert normalize({"harness_dirty": "x"})["harness_dirty"] is None
    assert normalize({"harness_dirty": True})["harness_dirty"] is True
    assert normalize({"event": "edit", "branch": "feat/x"}) == {
        "event": "edit",
        "branch": "feat/x",
    }


def test_aggregate_counts_metric_lines(report_aggregate: ModuleType) -> None:
    result = report_aggregate.aggregate(
        LOG, [], start=START, end=END, roots=no_roots(report_aggregate)
    )

    interval = result["interval"]
    assert result["window"] == {
        "start": "2026-11-06T15:00:00Z",
        "end": "2026-11-07T14:00:00Z",
    }
    assert result["skipped_lines"] == 3
    assert {key: interval[key] for key in INTERVAL_STATS} == INTERVAL_STATS
    assert interval["new_sessions"] == 3
    assert interval["reviews"] == [
        {
            "reviewer": "code-reviewer",
            "source": "log",
            "reports": 2,
            "format_ok": 1,
            "findings": {"high": 1, "medium": 0, "low": 0},
        },
        {
            "reviewer": "domain-reviewer",
            "source": "log",
            "reports": 1,
            "format_ok": 1,
            "findings": FOUND,
        },
    ]
    assert interval["panel_unreadable_sessions"] == []
    assert interval["session_files"] == []


def test_aggregate_builds_seven_day_trend(report_aggregate: ModuleType) -> None:
    trend = report_aggregate.aggregate(
        LOG, [], start=START, end=END, roots=no_roots(report_aggregate)
    )["trend"]

    assert [day["date"] for day in trend["days"]] == [
        f"2026-11-0{day}" for day in range(1, 8)
    ]
    assert trend["days"][:5] == [
        {"date": f"2026-11-0{day}", **EMPTY_DAY} for day in range(1, 6)
    ]
    assert trend["days"][5] == {"date": "2026-11-06", **OLD_LINE_DAY}
    assert trend["days"][6] == {"date": "2026-11-07", **INTERVAL_STATS}
    assert set(trend) == {"days", "by_harness_rev"}
    assert trend["by_harness_rev"] == [
        {
            "harness_rev": "aaa1111",
            "first_at": "2026-11-06T10:00:00Z",
            "last_at": "2026-11-07T02:00:00Z",
            "sessions": 2,
            "dirty_lines": 1,
            "stop_results": {"block": 1},
            "blocks": 1,
            "capped": 0,
            "edit": {"lines": 2, "clean_lines": 1, "pass_rate": 0.5},
            "rules": {"edit": {"E501": 2}, "stop": {"E501": 1}},
        },
        {
            "harness_rev": None,
            "first_at": "2026-11-07T01:05:00Z",
            "last_at": "2026-11-07T06:40:00Z",
            "sessions": 4,
            "dirty_lines": 0,
            "stop_results": {"allow": 2, "block": 1, "capped": 1, "error": 1},
            "blocks": 2,
            "capped": 1,
            "edit": {"lines": 2, "clean_lines": 1, "pass_rate": 0.5},
            "rules": {"edit": {"E501": 1}, "stop": {"E501": 4, "pytest": 1}},
        },
        {
            "harness_rev": "bbb2222",
            "first_at": "2026-11-07T01:10:00Z",
            "last_at": "2026-11-07T01:10:00Z",
            "sessions": 1,
            "dirty_lines": 0,
            "stop_results": {"block": 1},
            "blocks": 1,
            "capped": 0,
            "edit": no_edits(),
            "rules": {"edit": {}, "stop": {"E501": 1, "F401": 2}},
        },
    ]


def test_by_harness_rev_orders_null_first_and_groups_non_text_as_null(
    report_aggregate: ModuleType,
) -> None:
    log = [
        stop("07T01:00:00", "a", "claude", OPUS, result="allow", harness_rev="zzz"),
        stop("07T01:00:00", "b", "claude", OPUS, result="allow", harness_rev="aaa"),
        stop("07T01:00:00", "c", "claude", OPUS, result="allow", harness_rev=""),
        stop("07T01:00:00", "d", "claude", OPUS, result="allow", harness_rev=5),
        stop("07T01:00:00", "e", "claude", OPUS, result="allow"),
    ]

    items = report_aggregate.aggregate(
        log, [], start=None, end=END, roots=no_roots(report_aggregate)
    )["trend"]["by_harness_rev"]

    assert [(item["harness_rev"], item["sessions"]) for item in items] == [
        (None, 3),
        ("aaa", 1),
        ("zzz", 1),
    ]


def test_by_harness_rev_is_empty_without_lines(report_aggregate: ModuleType) -> None:
    trend = report_aggregate.aggregate(
        [], [], start=None, end=END, roots=no_roots(report_aggregate)
    )["trend"]

    assert trend["by_harness_rev"] == []


def test_aggregate_without_start_counts_every_session_as_new(
    report_aggregate: ModuleType,
) -> None:
    result = report_aggregate.aggregate(
        LOG, [], start=None, end=END, roots=no_roots(report_aggregate)
    )

    assert result["window"]["start"] is None
    assert result["interval"]["sessions"] == 4
    assert result["interval"]["new_sessions"] == 4


# TC-13 세션 기록 fixture


def stamp(minute: int) -> str:
    return f"2026-11-07T0{minute // 60}:{minute % 60:02d}:00.000Z"


def claude_tool(minute: int, name: str, tool_input: dict, cwd: str) -> dict:
    content = [{"type": "tool_use", "name": name, "input": tool_input}]
    return {
        "type": "assistant",
        "cwd": cwd,
        "timestamp": stamp(minute),
        "message": {"model": OPUS, "content": content},
    }


def codex_meta(
    minute: int,
    session: str,
    cwd: str,
    source: Any = "cli",
    originator: str | None = None,
) -> dict:
    payload = {"id": session, "cwd": cwd, "source": source}
    if originator is not None:
        payload["originator"] = originator
    return {"timestamp": stamp(minute), "type": "session_meta", "payload": payload}


def codex_command(minute: int, command: str) -> dict:
    item = {"type": "CommandExecution", "command": ["/bin/zsh", "-lc", command]}
    payload = {"type": "item_completed", "item": item}
    return {"timestamp": stamp(minute), "type": "event_msg", "payload": payload}


def codex_patch(minute: int, patch: str) -> dict:
    payload = {"type": "custom_tool_call", "name": "apply_patch", "input": patch}
    return {"timestamp": stamp(minute), "type": "response_item", "payload": payload}


def write_jsonl(path: Path, entries: list[dict], *, mtime: datetime = END) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    os.utime(path, (mtime.timestamp(), mtime.timestamp()))


class Home:
    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.repo = root / "repo"
        self.worktrees = self.home / ".herdr/worktrees/mosemo-server"
        for folder in (self.repo / "src", self.worktrees / "issue-9"):
            folder.mkdir(parents=True)
        self.abs_review = f"{self.worktrees}/issue-9-review"
        self.files: dict[str, Path] = {}

    def claude(self, name: str, entries: list[dict], **kwargs: Any) -> None:
        self.files[name] = self.home / f".claude/projects/-p/{name}.jsonl"
        write_jsonl(self.files[name], entries, **kwargs)

    def codex(self, name: str, entries: list[dict]) -> None:
        self.files[name] = self.home / f".codex/sessions/2026/11/07/{name}.jsonl"
        write_jsonl(self.files[name], entries)

    def roots(self, report_aggregate: ModuleType) -> Any:
        return report_aggregate.Roots(self.repo, self.worktrees)

    def args(self, log: Path) -> list[str]:
        return [
            "--log",
            str(log),
            "--start",
            "2026-11-06T15:00:00Z",
            "--end",
            "2026-11-07T14:00:00Z",
            "--home",
            str(self.home),
            "--main-checkout",
            str(self.repo),
            "--worktrees-root",
            str(self.worktrees),
        ]


TILDE_REVIEW = "~/.herdr/worktrees/mosemo-server/issue-9-review"
REVIEWER_LINE = metric(
    "07T03:00:00",
    "review",
    "codex-e",
    agent="codex",
    reviewer="code-reviewer",
    findings={"high": 0, "medium": 1, "low": 1},
    format_ok=True,
    via="subagent_stop",
)


@pytest.fixture
def home(tmp_path: Path) -> Home:
    home = Home(tmp_path)
    worktree = str(home.worktrees / "issue-9")
    write = {
        "file_path": f"{TILDE_REVIEW}/code.md",
        "content": "\n  REVIEW high=1 medium=0 low=2  \n\n본문",
    }
    heredoc = (
        f"cat > {home.abs_review}/domain.md <<'EOF'\n"
        "REVIEW high=0 medium=1 low=0\n세부\nEOF\necho done"
    )
    home.claude(
        "claude-a",
        [
            {"type": "user", "cwd": worktree, "timestamp": stamp(60)},
            claude_tool(70, "Write", write, worktree),
            claude_tool(80, "Bash", {"command": heredoc}, worktree),
        ],
    )
    home.claude(
        "claude-a/subagents/agent-1",
        [claude_tool(90, "Write", {**write, "file_path": "~/x"}, worktree)],
    )
    home.claude(
        "claude-old",
        [claude_tool(70, "Write", write, worktree)],
        mtime=datetime(2026, 11, 6, 14, tzinfo=UTC),
    )
    home.claude("claude-other", [claude_tool(70, "Write", write, str(home.home))])
    home.codex(
        "codex-b",
        [
            codex_meta(120, "codex-b", str(home.repo)),
            codex_command(
                130, f"cat > {TILDE_REVIEW}/security.md <<EOF\nnot review\nEOF"
            ),
            codex_command(
                140,
                f'cat > {TILDE_REVIEW}/security.md <<"EOF"\n'
                "REVIEW high=0 medium=0 low=0\nEOF",
            ),
            codex_patch(
                150,
                "*** Begin Patch\n"
                f"*** Add File: {home.abs_review}/arch.md\n"
                "+REVIEW high=2 medium=1 low=0\n+세부\n*** End Patch",
            ),
            codex_command(160, f"cat {TILDE_REVIEW}/code.md"),
        ],
    )
    home.codex(
        "codex-c",
        [
            codex_meta(170, "codex-c", str(home.repo / "src")),
            codex_command(
                175,
                "python -c \"from pathlib import Path; Path('"
                f"{TILDE_REVIEW}/perf.md').expanduser()"
                ".write_text('REVIEW high=0 medium=0 low=0')\"",
            ),
        ],
    )
    home.codex(
        "codex-d",
        [
            codex_meta(180, "codex-d", worktree),
            codex_command(
                185,
                f'herdr agent prompt "리뷰 결과를 {TILDE_REVIEW}/ux.md 에 써라"',
            ),
            codex_command(186, f"ls {TILDE_REVIEW}/ux.md"),
        ],
    )
    home.codex(
        "codex-e",
        [
            codex_meta(
                190,
                "codex-e",
                worktree,
                source={"subagent": {"agent_role": "code-reviewer"}},
            ),
            codex_command(
                195,
                f"cat > {TILDE_REVIEW}/code-reviewer.md <<'EOF'\n"
                "REVIEW high=0 medium=1 low=1\nEOF",
            ),
        ],
    )
    neighbor = home.home / ".herdr/worktrees/mosemo-server-old/issue-1"
    neighbor.mkdir(parents=True)
    home.codex(
        "codex-f",
        [
            codex_meta(200, "codex-f", str(neighbor)),
            codex_command(
                205,
                f"cat > {TILDE_REVIEW}/f.md <<'EOF'\nREVIEW high=9 medium=0 low=0\nEOF",
            ),
        ],
    )
    return home


def panel(stem: str, high: int, medium: int, low: int) -> dict[str, Any]:
    return {
        "reviewer": f"panel:{stem}",
        "source": "panel",
        "reports": 1,
        "format_ok": 1,
        "findings": {"high": high, "medium": medium, "low": low},
    }


def test_aggregate_extracts_panel_reviews(
    report_aggregate: ModuleType, home: Home
) -> None:
    transcripts = report_aggregate.read_transcripts(
        home.home, start=START, roots=[home.repo, home.worktrees]
    )

    result = report_aggregate.aggregate(
        [REVIEWER_LINE],
        transcripts,
        start=START,
        end=END,
        roots=home.roots(report_aggregate),
    )

    interval = result["interval"]
    assert interval["reviews"] == [
        {
            "reviewer": "code-reviewer",
            "source": "log",
            "reports": 1,
            "format_ok": 1,
            "findings": {"high": 0, "medium": 1, "low": 1},
        },
        panel("arch", 2, 1, 0),
        panel("code", 1, 0, 2),
        panel("domain", 0, 1, 0),
        panel("security", 0, 0, 0),
    ]
    assert interval["panel_unreadable_sessions"] == ["codex-c"]
    assert interval["session_files"] == sorted(
        str(home.files[name])
        for name in ("claude-a", "codex-b", "codex-c", "codex-d", "codex-e")
    )
    assert interval["sessions"] == 0


def test_aggregate_ignores_panel_writes_outside_window(
    report_aggregate: ModuleType, home: Home
) -> None:
    transcripts = report_aggregate.read_transcripts(
        home.home, start=None, roots=[home.repo, home.worktrees]
    )
    end = datetime(2026, 11, 7, 1, 15, tzinfo=UTC)

    interval = report_aggregate.aggregate(
        [], transcripts, start=None, end=end, roots=home.roots(report_aggregate)
    )["interval"]

    assert interval["reviews"] == [
        {**panel("code", 2, 0, 4), "reports": 2, "format_ok": 2}
    ]
    assert interval["session_files"] == sorted(
        str(home.files[name]) for name in ("claude-a", "claude-old")
    )


def test_main_prints_same_output_twice(
    report_aggregate: ModuleType,
    home: Home,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log = tmp_path / "metrics.jsonl"
    log.write_text("\n".join([*LOG, REVIEWER_LINE]) + "\n", encoding="utf-8")

    assert report_aggregate.main(home.args(log)) == 0
    first = capsys.readouterr().out
    assert report_aggregate.main(home.args(log)) == 0
    second = capsys.readouterr().out

    assert first == second
    assert first.endswith("}\n")
    output = json.loads(first)
    assert output["interval"]["sessions"] == 4
    assert output["interval"]["panel_unreadable_sessions"] == ["codex-c"]


# 세션 구분(진입점)


ENTRYPOINT_LOG = [
    stop("07T01:00:00", "claude-cli", "claude", OPUS, result="block"),
    edit("07T01:01:00", "claude-cli", "claude", OPUS, {}),
    stop("07T01:02:00", "claude-sdk", "claude", OPUS, result="block"),
    stop("07T01:03:00", "claude-sdk", "claude", OPUS, result="allow"),
    stop("07T01:04:00", "claude-ide", "claude", OPUS, result="allow"),
    edit("07T01:05:00", "codex-tui", "codex", GPT, {"E501": 1}),
    stop("07T01:06:00", "codex-exec", "codex", GPT, result="capped"),
    stop("07T01:07:00", "missing", "claude", OPUS, result="error"),
    stop("07T01:08:00", "", "claude", OPUS, result="block"),
]


def claude_entry(minute: int, cwd: str, entrypoint: str | None) -> dict:
    entry = {"type": "user", "cwd": cwd, "timestamp": stamp(minute)}
    if entrypoint is not None:
        entry["entrypoint"] = entrypoint
    return entry


def entrypoint_home(root: Path) -> Home:
    home = Home(root)
    cwd = str(home.repo)
    home.claude("claude-cli", [claude_entry(60, cwd, ""), claude_entry(61, cwd, "cli")])
    home.claude("claude-sdk", [claude_entry(62, cwd, "sdk-cli")])
    home.claude("claude-ide", [claude_entry(63, cwd, "claude-vscode")])
    home.codex("codex-tui", [codex_meta(64, "codex-tui", cwd, originator="codex-tui")])
    home.codex(
        "codex-exec",
        [codex_meta(65, "codex-exec", cwd, source="exec", originator="codex_exec")],
    )
    return home


def test_by_entrypoint_splits_sessions_by_transcript_entrypoint(
    report_aggregate: ModuleType, tmp_path: Path
) -> None:
    home = entrypoint_home(tmp_path)
    transcripts = report_aggregate.read_transcripts(
        home.home, start=START, roots=[home.repo, home.worktrees]
    )

    interval = report_aggregate.aggregate(
        ENTRYPOINT_LOG,
        transcripts,
        start=START,
        end=END,
        roots=home.roots(report_aggregate),
    )["interval"]

    groups = interval["by_entrypoint"]
    assert set(groups) == {"interactive", "non_interactive", "unknown"}
    assert {
        kind: (g["sessions"], g["entrypoints"], g["blocks"], g["edit"])
        for kind, g in groups.items()
    } == {
        "interactive": (
            2,
            {"cli": 1, "codex-tui": 1},
            1,
            {"lines": 2, "clean_lines": 1, "pass_rate": 0.5},
        ),
        "non_interactive": (2, {"codex_exec": 1, "sdk-cli": 1}, 1, no_edits()),
        "unknown": (2, {"claude-vscode": 1, "null": 1}, 2, no_edits()),
    }
    total: Counter[str] = Counter()
    for group in groups.values():
        total.update(group["stop_results"])
    assert dict(total) == interval["stop_results"]
    assert interval["sessions"] == 6


def test_by_entrypoint_uses_first_transcript_with_entrypoint(
    report_aggregate: ModuleType,
) -> None:
    transcripts = [
        report_aggregate.Transcript(
            path, "codex", [json.dumps(codex_meta(60, "cx", "/repo", originator=raw))]
        )
        for path, raw in (
            ("a.jsonl", None),
            ("b.jsonl", "codex_exec"),
            ("c.jsonl", "codex-tui"),
        )
    ]
    log = [stop("07T01:00:00", "cx", "codex", GPT, result="allow")]

    groups = report_aggregate.aggregate(
        log, transcripts, start=START, end=END, roots=no_roots(report_aggregate)
    )["interval"]["by_entrypoint"]

    assert groups["non_interactive"]["entrypoints"] == {"codex_exec": 1}
    assert groups["interactive"]["sessions"] == 0


def test_by_entrypoint_without_transcripts_is_all_unknown(
    report_aggregate: ModuleType,
) -> None:
    groups = report_aggregate.aggregate(
        LOG, [], start=START, end=END, roots=no_roots(report_aggregate)
    )["interval"]["by_entrypoint"]

    assert groups == {
        "interactive": {**EMPTY_DAY, "entrypoints": {}},
        "non_interactive": {**EMPTY_DAY, "entrypoints": {}},
        "unknown": {**INTERVAL_STATS, "entrypoints": {"null": 4}},
    }


def test_main_prints_same_output_twice_with_entrypoints(
    report_aggregate: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = entrypoint_home(tmp_path)
    log = tmp_path / "metrics.jsonl"
    log.write_text("\n".join(ENTRYPOINT_LOG) + "\n", encoding="utf-8")

    assert report_aggregate.main(home.args(log)) == 0
    first = capsys.readouterr().out
    assert report_aggregate.main(home.args(log)) == 0
    second = capsys.readouterr().out

    assert first == second
    groups = json.loads(first)["interval"]["by_entrypoint"]
    assert groups["non_interactive"]["entrypoints"] == {"codex_exec": 1, "sdk-cli": 1}


@pytest.mark.parametrize(
    "command",
    [
        f"echo x >> {TILDE_REVIEW}/a.md",
        f"echo x | tee -a '{TILDE_REVIEW}/a.md'",
        f"python -c \"Path('{TILDE_REVIEW}/a.md').expanduser().write_text('x')\"",
        f"python -c \"open(Path('{TILDE_REVIEW}/a.md').expanduser(), 'w')\"",
        f"python -c \"open('{TILDE_REVIEW}/a.md', mode='a')\"",
    ],
)
def test_write_forms_find_writes_to_path(
    report_aggregate: ModuleType, command: str
) -> None:
    assert list(report_aggregate.write_forms(command)) == [("issue-9-review", "a")]


@pytest.mark.parametrize(
    "command",
    [
        f"python -c \"open('/tmp/a', 'w').write('x')\"; cat {TILDE_REVIEW}/a.md",
        f"cat {TILDE_REVIEW}/a.md > /tmp/copy.md",
        f"python -c \"print(open('{TILDE_REVIEW}/a.md').read())\"",
    ],
)
def test_write_forms_ignore_reads_and_mentions(
    report_aggregate: ModuleType, command: str
) -> None:
    assert list(report_aggregate.write_forms(command)) == []


def test_panel_reviews_use_last_write_even_if_unreadable(
    report_aggregate: ModuleType,
) -> None:
    lines = [
        json.dumps(codex_meta(60, "cx", "/repo")),
        json.dumps(
            codex_command(
                70,
                f"cat > {TILDE_REVIEW}/a.md <<'EOF'\nREVIEW high=5 medium=0 low=0\nEOF",
            )
        ),
        json.dumps(
            codex_command(
                80, f"python -c \"Path('{TILDE_REVIEW}/a.md').write_text('x')\""
            )
        ),
    ]
    transcript = report_aggregate.Transcript("cx.jsonl", "codex", lines)

    reports, unreadable = report_aggregate.panel_reviews(
        [transcript], start=None, end=END
    )

    assert reports == []
    assert unreadable == ["cx"]


def test_trend_splits_days_at_seoul_midnight_and_stops_at_end(
    report_aggregate: ModuleType,
) -> None:
    log = [
        stop("06T14:59:59", "a", "claude", OPUS, result="allow"),
        stop("06T15:00:00", "b", "claude", OPUS, result="allow"),
        stop("07T14:30:00", "c", "claude", OPUS, result="allow"),
    ]

    days = report_aggregate.aggregate(
        log, [], start=None, end=END, roots=no_roots(report_aggregate)
    )["trend"]["days"]

    assert [(day["date"], day["sessions"]) for day in days[5:]] == [
        ("2026-11-06", 1),
        ("2026-11-07", 1),
    ]


def test_is_selected_compares_real_paths(
    report_aggregate: ModuleType, tmp_path: Path
) -> None:
    real = tmp_path / "mosemo-server"
    (real / "issue-1").mkdir(parents=True)
    (tmp_path / "mosemo-server-old").mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    assert report_aggregate.is_selected(str(link / "issue-1"), [real])
    assert report_aggregate.is_selected(str(real / "issue-1"), [link])
    assert not report_aggregate.is_selected(str(tmp_path / "mosemo-server-old"), [real])
    assert not report_aggregate.is_selected(None, [real])


def test_heredoc_writes_find_panel_path_after_other_redirects(
    report_aggregate: ModuleType,
) -> None:
    command = (
        f"mkdir -p {TILDE_REVIEW} 2>/dev/null && cat > {TILDE_REVIEW}/a.md <<'EOF'\n"
        "REVIEW high=0 medium=0 low=1\nEOF"
    )

    assert list(report_aggregate.heredoc_writes(command)) == [
        (("issue-9-review", "a"), "REVIEW high=0 medium=0 low=1")
    ]


def test_claude_writes_skip_content_blocks_that_are_not_objects(
    report_aggregate: ModuleType,
) -> None:
    write = {
        "file_path": f"{TILDE_REVIEW}/a.md",
        "content": "REVIEW high=0 medium=0 low=0",
    }
    entry = claude_tool(70, "Write", write, "/repo")
    entry["message"]["content"] = ["text", None, *entry["message"]["content"]]
    at = datetime(2026, 11, 7, 1, 10, tzinfo=UTC)

    assert list(report_aggregate.claude_writes(entry, at)) == [
        report_aggregate.PanelWrite(
            at, ("issue-9-review", "a"), "REVIEW high=0 medium=0 low=0"
        )
    ]


@pytest.mark.parametrize(
    "first_line",
    [
        f"cat > /tmp/notes.md <<'EOF' && echo x > {TILDE_REVIEW}/a.md",
        f"echo x > {TILDE_REVIEW}/a.md; cat <<'EOF' > /tmp/notes.md",
    ],
)
def test_shell_writes_keep_heredoc_body_with_its_own_command(
    report_aggregate: ModuleType, first_line: str
) -> None:
    command = f"{first_line}\nREVIEW high=9 medium=9 low=9\nEOF"

    writes = list(report_aggregate.shell_writes(command, END))

    assert writes == [report_aggregate.PanelWrite(END, ("issue-9-review", "a"), None)]


def test_review_stats_count_format_ok_without_findings(
    report_aggregate: ModuleType,
) -> None:
    reports = [
        {"reviewer": "r", "source": "log", "format_ok": True, "findings": None},
        {"reviewer": "r", "source": "log", "format_ok": True, "findings": FOUND},
        {"reviewer": "r", "source": "log", "format_ok": False, "findings": FOUND},
    ]

    assert report_aggregate.review_stats(reports) == [
        {
            "reviewer": "r",
            "source": "log",
            "reports": 3,
            "format_ok": 2,
            "findings": FOUND,
        }
    ]


# T3 기대 행동 집계(TC-A1~A8)와 전체 검증 통계(TC-A9)


MAIN = Path("/repo")
WORKTREES = Path("/home/.herdr/worktrees/mosemo-server")
RULES = {
    "src_tests_edit": "skill:implementation",
    "src_tests_edit_verify": "verify",
    "pr_create": "skill:pull-request",
    "plan_issue_create": "skill:engineering-planning",
    "adr_edit": "skill:architecture-decision",
    "prd_edit": "skill:specification",
    "glossary_edit": "skill:ubiquitous-language",
}


def counts(
    rule_id: str, sessions: int = 0, missing: list[str] | None = None, no_stop: int = 0
) -> dict[str, Any]:
    missing = missing or []
    return {
        "expect": RULES[rule_id],
        "sessions": sessions,
        "missing": len(missing),
        "missing_sessions": missing,
        "no_stop": no_stop,
    }


def claude_session(report_aggregate: ModuleType, session: str, entries: list) -> Any:
    lines = [json.dumps(entry) for entry in entries]
    return report_aggregate.Transcript(f"/p/{session}.jsonl", "claude", lines)


def codex_session(report_aggregate: ModuleType, session: str, entries: list) -> Any:
    lines = [json.dumps(entry) for entry in entries]
    return report_aggregate.Transcript(f"/c/{session}.jsonl", "codex", lines)


def edit_tool(minute: int, path: str, name: str = "Edit", cwd: Path = MAIN) -> dict:
    key = "notebook_path" if name == "NotebookEdit" else "file_path"
    return claude_tool(minute, name, {key: path}, str(cwd))


def bash_tool(minute: int, command: str) -> dict:
    return claude_tool(minute, "Bash", {"command": command}, str(MAIN))


def skills_stop(session: str, skills: list[str], agent: str = "claude") -> str:
    return stop("07T05:00:00", session, agent, OPUS, result="allow", skills=skills)


def verify_line(at: str, session: str, **fields: Any) -> str:
    fields = {"agent": "claude", "ok": True, "failed": [], "not_run": [], **fields}
    return metric(at, "verify", session, **fields)


def run_aggregate(
    report_aggregate: ModuleType, log: list[str], transcripts: list
) -> dict[str, Any]:
    roots = report_aggregate.Roots(MAIN, WORKTREES)
    return report_aggregate.aggregate(
        log, transcripts, start=START, end=END, roots=roots
    )


def expected(
    report_aggregate: ModuleType, log: list[str], transcripts: list
) -> dict[str, Any]:
    return run_aggregate(report_aggregate, log, transcripts)["interval"][
        "expected_actions"
    ]


def test_expected_actions_and_verify_keys_without_input(
    report_aggregate: ModuleType,
) -> None:
    interval = run_aggregate(report_aggregate, [], [])["interval"]

    assert interval["expected_actions"] == {
        agent: {rule_id: counts(rule_id) for rule_id in RULES}
        for agent in ("claude", "codex")
    }
    empty = {"runs": 0, "ok_runs": 0, "failed_checks": [], "not_run": {}}
    assert interval["verify"] == {"claude": empty, "unknown": empty}


def test_src_edit_without_skill_or_verify_is_missing(
    report_aggregate: ModuleType,
) -> None:
    transcript = claude_session(
        report_aggregate, "s1", [edit_tool(60, f"{MAIN}/src/mosemo/x.py")]
    )

    claude = expected(report_aggregate, [skills_stop("s1", [])], [transcript])["claude"]

    assert claude["src_tests_edit"] == counts("src_tests_edit", 1, ["s1"])
    assert claude["src_tests_edit_verify"] == counts("src_tests_edit_verify", 1, ["s1"])
    assert claude["pr_create"] == counts("pr_create")


@pytest.mark.parametrize(
    ("edits", "verify_at", "verify_missing"),
    [
        ([60], "07T01:05:00", []),
        ([60], "07T01:00:00", []),
        ([60], "07T00:59:00", ["s1"]),
        ([60, 80], "07T01:05:00", ["s1"]),
    ],
    ids=["after", "same-second", "before", "edited-again"],
)
def test_skill_and_verify_after_last_edit_satisfy_rules(
    report_aggregate: ModuleType,
    edits: list[int],
    verify_at: str,
    verify_missing: list[str],
) -> None:
    entries = [edit_tool(minute, f"{MAIN}/tests/unit/x.py") for minute in edits]
    log = [
        skills_stop("s1", ["implementation"]),
        verify_line(verify_at, "s1", ok=False, failed=["ruff check"]),
    ]

    claude = expected(
        report_aggregate, log, [claude_session(report_aggregate, "s1", entries)]
    )["claude"]

    assert claude["src_tests_edit"] == counts("src_tests_edit", 1)
    assert claude["src_tests_edit_verify"] == counts(
        "src_tests_edit_verify", 1, verify_missing
    )


@pytest.mark.parametrize(
    "patch_input",
    [
        "*** Begin Patch\n*** Update File: tests/unit/x.py\n@@\n-a\n+b\n*** End Patch",
        (
            'text(await tools.apply_patch("*** Begin Patch\\n'
            '*** Update File: tests/unit/x.py\\n@@\\n-a\\n+b\\n*** End Patch"));'
        ),
    ],
    ids=["apply_patch", "exec-wrapped"],
)
def test_codex_patch_counts_only_under_codex(
    report_aggregate: ModuleType, patch_input: str
) -> None:
    transcript = codex_session(
        report_aggregate,
        "cx",
        [codex_meta(59, "cx", str(WORKTREES / "w1")), codex_patch(60, patch_input)],
    )

    result = expected(report_aggregate, [skills_stop("cx", [], "codex")], [transcript])

    assert result["codex"]["src_tests_edit"] == counts("src_tests_edit", 1, ["cx"])
    assert result["claude"]["src_tests_edit"] == counts("src_tests_edit")


def test_codex_actions_cut_patch_paths_at_escaped_newline(
    report_aggregate: ModuleType,
) -> None:
    code = (
        'text(await tools.apply_patch("*** Begin Patch\\n*** Add File: a.md\\n+x\\n'
        '*** Delete File: /repo/b.py\\n*** End Patch"));'
    )

    actions = list(report_aggregate.codex_actions(codex_patch(60, code)))

    assert actions == [("command", code), ("edit", "a.md"), ("edit", "/repo/b.py")]


def test_edit_paths_inside_either_root_count(report_aggregate: ModuleType) -> None:
    transcripts = [
        claude_session(
            report_aggregate, "inside", [edit_tool(60, f"{WORKTREES}/w1/src/a.py")]
        ),
        claude_session(report_aggregate, "outside", [edit_tool(60, "/tmp/src/a.py")]),
    ]
    log = [skills_stop("inside", []), skills_stop("outside", [])]

    claude = expected(report_aggregate, log, transcripts)["claude"]

    assert claude["src_tests_edit"] == counts("src_tests_edit", 1, ["inside"])


@pytest.mark.parametrize(
    ("path", "cwd", "relative"),
    [
        ("src/a.py", MAIN, "src/a.py"),
        ("./tests/../src/a.py", WORKTREES / "w1", "src/a.py"),
        (f"{MAIN}/docs/adr/x.md", WORKTREES / "w1", "docs/adr/x.md"),
        (f"{WORKTREES}/w1/src/a.py", MAIN, "src/a.py"),
        (f"{WORKTREES}/w1", MAIN, None),
        ("/repository/src/a.py", MAIN, None),
        ("src/a.py", None, None),
    ],
)
def test_repo_path_strips_main_or_one_worktree_level(
    report_aggregate: ModuleType, path: str, cwd: Path | None, relative: str | None
) -> None:
    roots = report_aggregate.Roots(MAIN, WORKTREES)
    folder = str(cwd) if cwd is not None else None

    assert report_aggregate.repo_path(path, cwd=folder, roots=roots) == relative


def test_command_rules_match_pr_and_plan_issue_creation(
    report_aggregate: ModuleType,
) -> None:
    function_call = {
        "type": "function_call",
        "name": "exec_command",
        "arguments": json.dumps({"cmd": 'gh issue create --title "[Plan] y"'}),
    }
    transcripts = [
        claude_session(
            report_aggregate,
            "pr",
            [bash_tool(60, "env -u GH_TOKEN gh pr create --title t --body-file b")],
        ),
        claude_session(
            report_aggregate,
            "plan",
            [bash_tool(60, 'gh issue create --title "[Plan] x" --body-file b')],
        ),
        claude_session(
            report_aggregate, "fix", [bash_tool(60, 'gh issue create --title "fix"')]
        ),
        codex_session(
            report_aggregate,
            "cx",
            [
                codex_meta(59, "cx", str(MAIN)),
                codex_command(60, "gh pr create --fill"),
                {
                    "timestamp": stamp(61),
                    "type": "response_item",
                    "payload": function_call,
                },
            ],
        ),
    ]
    log = [
        skills_stop("pr", []),
        skills_stop("plan", ["engineering-planning"]),
        skills_stop("fix", []),
        skills_stop("cx", ["pull-request"], "codex"),
    ]

    result = expected(report_aggregate, log, transcripts)

    assert result["claude"]["pr_create"] == counts("pr_create", 1, ["pr"])
    assert result["claude"]["plan_issue_create"] == counts("plan_issue_create", 1)
    assert result["codex"]["pr_create"] == counts("pr_create", 1)
    assert result["codex"]["plan_issue_create"] == counts(
        "plan_issue_create", 1, ["cx"]
    )


def test_document_edits_match_their_rules(report_aggregate: ModuleType) -> None:
    entries = [
        edit_tool(60, f"{MAIN}/docs/adr/0016-x.md"),
        edit_tool(61, f"{MAIN}/docs/prd/a.md", "Write"),
        edit_tool(62, f"{MAIN}/docs/ubiquitous-language.md", "MultiEdit"),
        edit_tool(63, f"{MAIN}/docs/index.md", "NotebookEdit"),
    ]
    log = [skills_stop("s1", ["architecture-decision"])]

    claude = expected(
        report_aggregate, log, [claude_session(report_aggregate, "s1", entries)]
    )["claude"]

    assert claude["adr_edit"] == counts("adr_edit", 1)
    assert claude["prd_edit"] == counts("prd_edit", 1, ["s1"])
    assert claude["glossary_edit"] == counts("glossary_edit", 1, ["s1"])
    assert claude["src_tests_edit"] == counts("src_tests_edit")


def test_notebook_edit_reads_notebook_path(report_aggregate: ModuleType) -> None:
    entries = [edit_tool(60, f"{MAIN}/tests/a.ipynb", "NotebookEdit")]

    claude = expected(
        report_aggregate,
        [skills_stop("s1", [])],
        [claude_session(report_aggregate, "s1", entries)],
    )["claude"]

    assert claude["src_tests_edit"] == counts("src_tests_edit", 1, ["s1"])


def test_sessions_without_stop_outside_window_or_review_subagent(
    report_aggregate: ModuleType,
) -> None:
    late = {**edit_tool(0, f"{MAIN}/src/a.py"), "timestamp": "2026-11-07T15:00:00Z"}
    reviewer = {"subagent": {"agent_role": "code-reviewer"}}
    patch = "*** Begin Patch\n*** Update File: src/a.py\n*** End Patch"
    transcripts = [
        claude_session(
            report_aggregate, "no-stop", [edit_tool(60, f"{MAIN}/src/a.py")]
        ),
        claude_session(report_aggregate, "late", [late]),
        codex_session(
            report_aggregate,
            "review",
            [
                codex_meta(59, "review", str(MAIN), source=reviewer),
                codex_patch(60, patch),
            ],
        ),
    ]
    log = [skills_stop("late", []), skills_stop("review", [], "codex")]

    result = expected(report_aggregate, log, transcripts)

    assert result["claude"]["src_tests_edit"] == counts("src_tests_edit", no_stop=1)
    assert result["codex"]["src_tests_edit"] == counts("src_tests_edit")


VERIFY_LOG = [
    verify_line("07T02:00:00", "s1"),
    verify_line("07T02:10:00", "s1", ok=False, failed=["ty check", "poe coverage", 3]),
    verify_line(
        "07T02:20:00",
        "s1",
        ok=False,
        failed=["poe coverage", "git diff --check"],
        not_run=[{"check": "poe coverage", "reason": "docker"}, "broken"],
    ),
    verify_line("07T02:30:00", "s9", agent="unknown", ok=False, failed=None),
    verify_line("07T20:00:00", "s1", ok=False, failed=["ruff check"]),
]


def test_verify_stats_count_runs_failed_checks_and_not_run(
    report_aggregate: ModuleType,
) -> None:
    verify = run_aggregate(report_aggregate, VERIFY_LOG, [])["interval"]["verify"]

    assert verify == {
        "claude": {
            "runs": 3,
            "ok_runs": 1,
            "failed_checks": [
                {"check": "poe coverage", "count": 2},
                {"check": "git diff --check", "count": 1},
                {"check": "ty check", "count": 1},
            ],
            "not_run": {"docker": 1},
        },
        "unknown": {"runs": 1, "ok_runs": 0, "failed_checks": [], "not_run": {}},
    }


def test_verify_lines_leave_existing_keys_unchanged(
    report_aggregate: ModuleType,
) -> None:
    without = run_aggregate(report_aggregate, LOG, [])
    with_verify = run_aggregate(report_aggregate, [*LOG, *VERIFY_LOG], [])
    for result in (without, with_verify):
        del result["interval"]["verify"]

    assert with_verify == without

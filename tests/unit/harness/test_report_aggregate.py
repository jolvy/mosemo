import json
import os
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
    result = report_aggregate.aggregate(LOG, [], start=START, end=END)

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
    trend = report_aggregate.aggregate(LOG, [], start=START, end=END)["trend"]

    assert [day["date"] for day in trend["days"]] == [
        f"2026-11-0{day}" for day in range(1, 8)
    ]
    assert trend["days"][:5] == [
        {"date": f"2026-11-0{day}", **EMPTY_DAY} for day in range(1, 6)
    ]
    assert trend["days"][5] == {"date": "2026-11-06", **OLD_LINE_DAY}
    assert trend["days"][6] == {"date": "2026-11-07", **INTERVAL_STATS}
    assert trend["harness_rev_changes"] == [
        {"at": "2026-11-06T10:00:00Z", "harness_rev": "aaa1111"},
        {"at": "2026-11-07T01:10:00Z", "harness_rev": "bbb2222"},
        {"at": "2026-11-07T02:00:00Z", "harness_rev": "aaa1111"},
    ]


def test_aggregate_without_start_counts_every_session_as_new(
    report_aggregate: ModuleType,
) -> None:
    result = report_aggregate.aggregate(LOG, [], start=None, end=END)

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


def codex_meta(minute: int, session: str, cwd: str, source: Any = "cli") -> dict:
    payload = {"id": session, "cwd": cwd, "source": source}
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
        [REVIEWER_LINE], transcripts, start=START, end=END
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

    interval = report_aggregate.aggregate([], transcripts, start=None, end=end)[
        "interval"
    ]

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

    days = report_aggregate.aggregate(log, [], start=None, end=END)["trend"]["days"]

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

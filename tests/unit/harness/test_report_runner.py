"""일일 보고서 실행기(`run_daily.py`)와 launchd plist 생성(`install_launchd.py`).

git·subprocess를 부르지 않는다. 분석 명령과 알림은 호출 인자를 기록하는 대역이다.
"""

import json
import os
import plistlib
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

import pytest

NOW = datetime(2026, 11, 8, 0, 30, tzinfo=UTC)  # 2026-11-08 09:30 Asia/Seoul
BOUNDARY = "2026-11-07T00:00:00Z"
RESULT = "## 1. 결과\n가\n## 2. 최근 며칠의 양상\n나\n## 3. 개선해야 할 점\n다"
SUCCESS = json.dumps({"result": RESULT, "total_cost_usd": 1.23, "is_error": False})


def stop(at: str, session: str) -> str:
    line = {
        "at": at,
        "event": "stop",
        "session": session,
        "agent": "claude",
        "model": "claude-opus-5-5",
        "result": "allow",
        "rules": {},
    }
    return json.dumps(line)


def window_stops(sessions: list[str]) -> list[str]:
    return [stop("2026-11-07T10:00:00Z", session) for session in sessions]


class Home(NamedTuple):
    root: Path
    reports: Path
    repository: Path
    worktrees: Path
    log: Path

    def write_log(self, lines: list[str]) -> None:
        self.log.write_text("\n".join(lines) + "\n")

    def transcript(self, name: str, cwd: Path) -> Path:
        path = self.root / ".claude/projects/project" / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp": "2026-11-07T10:00:00Z", "cwd": str(cwd)}
        path.write_text(json.dumps(entry) + "\n")
        os.utime(path, (NOW.timestamp(), NOW.timestamp()))
        return path

    def files(self) -> set[Path]:
        return {path for path in self.root.rglob("*") if path.is_file()}

    def runs(self) -> list[dict[str, Any]]:
        lines = (self.reports / ".runs.jsonl").read_text().splitlines()
        return [json.loads(line) for line in lines]


class Recorder:
    def __init__(self, runner: ModuleType, analysis: tuple[int, str]) -> None:
        self.runner = runner
        self.analysis = analysis
        self.analyses: list[dict[str, Any]] = []
        self.notifications: list[tuple[str, str]] = []

    def analyze(self, args: list[str], *, stdin: str, cwd: Path) -> Any:
        self.analyses.append({"args": list(args), "stdin": stdin, "cwd": cwd})
        return self.runner.Analysis(*self.analysis)

    def notify(self, title: str, body: str) -> None:
        self.notifications.append((title, body))


@pytest.fixture
def home(tmp_path: Path) -> Home:
    root = tmp_path / "home"
    repository = root / "repo"
    (repository / ".git").mkdir(parents=True)
    worktrees = root / ".herdr/worktrees/mosemo-server"
    worktrees.mkdir(parents=True)
    log = repository / ".git/mosemo-agent-metrics.jsonl"
    return Home(root, root / "mosemo-reports", repository, worktrees, log)


def config(runner: ModuleType, home: Home, reports: Path | None = None) -> Any:
    return runner.Config(
        reports=reports or home.reports,
        home=home.root,
        metrics_log=home.log,
        main_checkout=home.repository,
        worktrees_root=home.worktrees,
    )


def run(
    runner: ModuleType, home: Home, analysis: tuple[int, str] = (0, SUCCESS)
) -> tuple[int, Recorder]:
    recorder = Recorder(runner, analysis)
    code = runner.run(
        config(runner, home),
        now=NOW,
        analyze=recorder.analyze,
        notify=recorder.notify,
    )
    return code, recorder


def write_state(home: Home) -> bytes:
    home.reports.mkdir(parents=True, exist_ok=True)
    state = home.reports / ".state.json"
    state.write_text(json.dumps({"boundary": BOUNDARY}) + "\n")
    return state.read_bytes()


def five_new_sessions(home: Home) -> list[Path]:
    write_state(home)
    home.write_log(window_stops([f"n{i}" for i in range(5)]))
    return [
        home.transcript("inside-main", home.repository),
        home.transcript("inside-worktree", home.worktrees / "issue-1"),
        home.transcript("outside", home.root / "aico"),
    ]


# TC-14 건너뛰기


def test_skips_without_analysis_when_new_sessions_are_fewer_than_five(
    report_runner: ModuleType, home: Home
) -> None:
    state = write_state(home)
    continuing = [stop("2026-11-06T10:00:00Z", s) for s in ("o1", "o2")]
    home.write_log(continuing + window_stops(["o1", "o2", "n1", "n2", "n3", "n4"]))

    code, recorder = run(report_runner, home)

    assert code == 0
    assert recorder.analyses == []
    assert recorder.notifications == []
    assert list(home.reports.glob("*.md")) == []
    assert (home.reports / ".state.json").read_bytes() == state
    last = home.runs()[-1]
    assert last["status"] == "skipped"
    assert (last["start"], last["end"]) == (BOUNDARY, "2026-11-08T00:30:00Z")
    assert last["sessions"] == 6
    assert (last["cost_usd"], last["report"], last["error"]) == (None, None, None)


def test_first_run_uses_seven_days_before_now(
    report_runner: ModuleType, home: Home
) -> None:
    home.write_log(window_stops(["n1"]))

    code, _ = run(report_runner, home)

    assert code == 0
    assert home.runs()[-1]["start"] == "2026-11-01T00:30:00Z"
    assert not (home.reports / ".state.json").exists()


# TC-15 성공 실행


def test_writes_report_moves_boundary_and_notifies(
    report_runner: ModuleType, home: Home
) -> None:
    inside_main, inside_worktree, _ = five_new_sessions(home)
    before = home.files()

    code, recorder = run(report_runner, home)

    report = home.reports / "2026-11-08.md"
    assert code == 0
    content = report.read_text()
    assert content.startswith("# mosemo 에이전트 일일 보고서 2026-11-08\n")
    first, second, third, info = (
        content.index(heading) for heading in (*report_runner.HEADINGS, "## 실행 정보")
    )
    assert first < second < third < info
    assert content.index("### 요약") < content.index("가") < second
    assert "| 5 | 0 | 0 | 0 | - |" in content[first:second]
    assert content.index("### 7일 추세") < content.index("나") < third
    assert "| 2026-11-07 | 5 | 0 | 0 | - | 0 | 0 |" in content[second:third]
    assert "다" in content[third:info]
    assert "1.23 USD" in content[info:]
    assert "claude-opus-5-5" in content[info:]
    state = json.loads((home.reports / ".state.json").read_text())
    assert state == {"boundary": "2026-11-08T00:30:00Z"}
    last = home.runs()[-1]
    assert (last["status"], last["cost_usd"]) == ("report", 1.23)
    assert last["report"] == str(report)
    assert last["error"] is None
    assert recorder.notifications == [(report_runner.SUCCESS_TITLE, str(report))]
    copies = {
        report_runner.copy_name(str(inside_main)),
        report_runner.copy_name(str(inside_worktree)),
    }
    sessions = home.reports / ".work/sessions"
    assert {path.name for path in sessions.iterdir()} == copies
    assert all(path.is_relative_to(home.reports) for path in home.files() - before)
    [analysis] = recorder.analyses
    assert analysis["args"] == report_runner.analysis_command()
    assert analysis["cwd"] == home.reports / ".work"
    assert analysis["stdin"].startswith(report_runner.SKILL.read_text().rstrip())
    assert str(home.reports / ".work/aggregate.json") in analysis["stdin"]
    assert (
        f"{sessions / report_runner.copy_name(str(inside_main))} ← "
        in (analysis["stdin"])
    )
    aggregate = json.loads((home.reports / ".work/aggregate.json").read_text())
    assert aggregate["interval"]["new_sessions"] == 5


def test_adds_seoul_time_when_report_of_same_date_exists(
    report_runner: ModuleType, home: Home
) -> None:
    five_new_sessions(home)
    (home.reports / "2026-11-08.md").write_text("earlier\n")

    code, _ = run(report_runner, home)

    assert code == 0
    assert (home.reports / "2026-11-08.md").read_text() == "earlier\n"
    assert (home.reports / "2026-11-08-093000.md").is_file()


def test_recreates_work_folder(report_runner: ModuleType, home: Home) -> None:
    five_new_sessions(home)
    stale = home.reports / ".work/sessions/stale.jsonl"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}\n")

    run(report_runner, home)

    assert not stale.exists()


# TC-16 실패 실행


@pytest.mark.parametrize(
    ("analysis", "reason"),
    [
        ((1, ""), "종료 코드 1"),
        ((0, json.dumps({"result": "한도", "is_error": True})), "is_error"),
        ((0, json.dumps({"result": "제목 없음", "is_error": False})), "절 제목"),
        ((0, "not json"), "JSON"),
        (
            (0, json.dumps({"result": RESULT.replace("## 1. 결과", "## 결과")})),
            "## 1. 결과",
        ),
    ],
)
def test_failed_analysis_keeps_state_and_notifies(
    report_runner: ModuleType,
    home: Home,
    analysis: tuple[int, str],
    reason: str,
) -> None:
    five_new_sessions(home)
    state = (home.reports / ".state.json").read_bytes()

    code, recorder = run(report_runner, home, analysis)

    assert code == 1
    assert list(home.reports.glob("*.md")) == []
    assert (home.reports / ".state.json").read_bytes() == state
    last = home.runs()[-1]
    assert (last["status"], last["cost_usd"], last["report"]) == ("failed", None, None)
    assert reason in last["error"]
    assert "\n" not in last["error"]
    [(title, body)] = recorder.notifications
    assert title == report_runner.FAILURE_TITLE
    assert reason in body
    assert str(home.reports / ".runs.jsonl") in body


def test_missing_metrics_log_is_a_failure(
    report_runner: ModuleType, home: Home
) -> None:
    code, recorder = run(report_runner, home)

    assert code == 1
    assert home.runs()[-1]["status"] == "failed"
    assert [title for title, _ in recorder.notifications] == [
        report_runner.FAILURE_TITLE
    ]


def test_state_write_failure_removes_report(
    report_runner: ModuleType, home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    five_new_sessions(home)
    original = Path.write_text

    def write_text(path: Path, *args: Any, **kwargs: Any) -> int:
        if path.name == ".state.json":
            message = "disk full"
            raise OSError(message)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)

    code, recorder = run(report_runner, home)

    assert code == 1
    assert list(home.reports.glob("*.md")) == []
    assert home.runs()[-1]["error"] == "disk full"
    assert len(recorder.notifications) == 1


def test_unwritable_reports_folder_still_notifies(
    report_runner: ModuleType, home: Home
) -> None:
    home.reports.write_text("not a folder\n")

    code, recorder = run(report_runner, home)

    assert code == 1
    assert [title for title, _ in recorder.notifications] == [
        report_runner.FAILURE_TITLE
    ]


def no_git(directory: Path) -> Path:
    message = f"git 없음: {directory}"
    raise FileNotFoundError(message)


def test_prerun_failure_is_recorded_and_notified(
    report_runner: ModuleType, tmp_path: Path
) -> None:
    reports = tmp_path / "reports"
    recorder = Recorder(report_runner, (0, SUCCESS))

    code = report_runner.main(
        ["--reports-dir", str(reports)],
        now=NOW,
        common_dir=no_git,
        analyze=recorder.analyze,
        notify=recorder.notify,
    )

    assert code == 1
    [line] = [
        json.loads(text) for text in (reports / ".runs.jsonl").read_text().splitlines()
    ]
    assert line["status"] == "failed"
    assert (line["start"], line["end"]) == (None, "2026-11-08T00:30:00Z")
    assert "git 없음" in line["error"]
    assert recorder.analyses == []
    [(title, body)] = recorder.notifications
    assert title == report_runner.FAILURE_TITLE
    assert str(reports / ".runs.jsonl") in body


def test_prerun_failure_inside_checkout_writes_nothing(
    report_runner: ModuleType,
) -> None:
    reports = report_runner.CHECKOUT / "never-created-reports"
    recorder = Recorder(report_runner, (0, SUCCESS))

    code = report_runner.main(
        ["--reports-dir", str(reports)],
        now=NOW,
        common_dir=no_git,
        analyze=recorder.analyze,
        notify=recorder.notify,
    )

    assert code == 1
    assert not reports.exists()
    assert [title for title, _ in recorder.notifications] == [
        report_runner.FAILURE_TITLE
    ]


def test_main_runs_with_config_from_common_git_dir(
    report_runner: ModuleType, home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    five_new_sessions(home)
    monkeypatch.setenv("HOME", str(home.root))
    monkeypatch.delenv("MOSEMO_METRICS_LOG", raising=False)
    recorder = Recorder(report_runner, (0, SUCCESS))

    code = report_runner.main(
        ["--reports-dir", str(home.reports)],
        now=NOW,
        common_dir=lambda _: home.repository / ".git",
        analyze=recorder.analyze,
        notify=recorder.notify,
    )

    assert code == 0
    assert home.runs()[-1]["status"] == "report"


def test_unknown_home_notifies_without_writing(
    report_runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_home(path: Path) -> Path:
        message = "Could not determine home directory."
        raise RuntimeError(message)

    monkeypatch.setattr(Path, "expanduser", no_home)
    recorder = Recorder(report_runner, (0, SUCCESS))

    code = report_runner.main(
        [], now=NOW, common_dir=no_git, analyze=recorder.analyze, notify=recorder.notify
    )

    assert code == 1
    assert list(tmp_path.iterdir()) == []
    [(title, body)] = recorder.notifications
    assert title == report_runner.FAILURE_TITLE
    assert "home directory" in body


def test_runs_write_failure_rolls_back_report_and_boundary(
    report_runner: ModuleType, home: Home, monkeypatch: pytest.MonkeyPatch
) -> None:
    five_new_sessions(home)
    state = (home.reports / ".state.json").read_bytes()
    original = Path.open

    def open_file(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name == ".runs.jsonl":
            message = "disk full"
            raise OSError(message)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)

    code, recorder = run(report_runner, home)

    assert code == 1
    assert list(home.reports.glob("*.md")) == []
    assert (home.reports / ".state.json").read_bytes() == state
    assert [title for title, _ in recorder.notifications] == [
        report_runner.FAILURE_TITLE
    ]


def test_build_config_prefers_metrics_env(report_runner: ModuleType) -> None:
    common = Path("/Users/x/repo/.git")
    home = Path("/Users/x")

    default = report_runner.build_config(
        Path("/r"), common=common, home=home, metrics=None
    )
    custom = report_runner.build_config(
        Path("/r"), common=common, home=home, metrics="/tmp/m.jsonl"
    )

    assert default.metrics_log == common / "mosemo-agent-metrics.jsonl"
    assert default.main_checkout == Path("/Users/x/repo")
    assert default.worktrees_root == home / ".herdr/worktrees/mosemo-server"
    assert custom.metrics_log == Path("/tmp/m.jsonl")


def test_sections_must_follow_heading_order(report_runner: ModuleType) -> None:
    reordered = "## 2. 최근 며칠의 양상\n나\n## 1. 결과\n가\n## 3. 개선해야 할 점\n다"

    with pytest.raises(report_runner.RunError):
        report_runner.split_sections(reordered)
    assert report_runner.split_sections(f"머리말\n{RESULT}\n") == ["가", "나", "다"]


# TC-17 분석 명령 도구 제한과 쓰기 대상


def option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def test_analysis_command_allows_only_read_tools(report_runner: ModuleType) -> None:
    args = report_runner.analysis_command()

    for name in ("--tools", "--allowedTools"):
        assert set(option(args, name).split(",")) == {"Read", "Grep", "Glob"}
    joined = " ".join(args)
    for forbidden in ("Bash", "gh", "git push", "--add-dir"):
        assert forbidden not in args
        assert forbidden not in joined
    assert option(args, "--model") == "claude-opus-5-5"
    assert "--strict-mcp-config" in args


def test_write_targets_are_under_reports(report_runner: ModuleType) -> None:
    reports = Path("/Users/x/mosemo-reports")

    targets = report_runner.write_targets(reports, NOW)

    assert targets
    assert all(path.is_relative_to(reports) for path in targets)


@pytest.mark.parametrize(
    ("reports", "allowed"),
    [
        ("/Users/x/mosemo-reports", True),
        ("/Users/x/repo/reports", False),
        ("/Users/x/repo", False),
        ("/Users/x/repo-reports", True),
    ],
)
def test_reports_must_be_outside_repository(
    report_runner: ModuleType, reports: str, allowed: bool
) -> None:
    assert report_runner.is_outside(Path(reports), Path("/Users/x/repo")) is allowed


def test_reports_inside_repository_writes_nothing(
    report_runner: ModuleType, home: Home
) -> None:
    home.write_log(window_stops([f"n{i}" for i in range(5)]))
    before = home.files()
    recorder = Recorder(report_runner, (0, SUCCESS))

    code = report_runner.run(
        config(report_runner, home, home.repository / "reports"),
        now=NOW,
        analyze=recorder.analyze,
        notify=recorder.notify,
    )

    assert code == 1
    assert home.files() == before
    assert not (home.repository / "reports").exists()
    assert recorder.analyses == []
    assert [title for title, _ in recorder.notifications] == [
        report_runner.FAILURE_TITLE
    ]


def test_copy_name_joins_original_path(report_runner: ModuleType) -> None:
    name = report_runner.copy_name("/Users/x/.claude/projects/p/a.jsonl")

    assert name == "Users_x_.claude_projects_p_a.jsonl"


def test_applescript_string_escapes_quotes(report_runner: ModuleType) -> None:
    assert report_runner.applescript_string('a "b" \\c') == '"a \\"b\\" \\\\c"'


# TC-18 launchd plist 생성


def tools(found: dict[str, str]) -> Any:
    return found.get


def test_writes_same_plist_twice(report_launchd: ModuleType, tmp_path: Path) -> None:
    plist = tmp_path / "agent.plist"
    reports = tmp_path / "reports"
    repository = tmp_path / "repo"
    which = tools({"uv": "/opt/uv/bin/uv", "claude": "/opt/claude/bin/claude"})
    argv = ["--plist", str(plist), "--reports-dir", str(reports)]

    assert report_launchd.main(argv, which=which, repository=repository) == 0
    first = plist.read_bytes()
    assert report_launchd.main(argv, which=which, repository=repository) == 0

    assert plist.read_bytes() == first
    data = plistlib.loads(first)
    logs = reports.resolve() / ".logs"
    assert data["Label"] == "dev.jolvy.mosemo-agent-report"
    assert data["StartCalendarInterval"] == {"Hour": 9, "Minute": 0}
    assert data["ProgramArguments"][0:2] == ["/bin/sh", "-c"]
    command = data["ProgramArguments"][2]
    assert command.startswith(
        f"/opt/uv/bin/uv run --locked --directory {repository} "
        "python .codex/reports/run_daily.py"
    )
    assert "/usr/bin/osascript -e" in command
    assert f"{logs}/launchd.err" in command
    assert "mosemo 보고서 실패" in command
    assert command.endswith("exit $code")
    assert data["EnvironmentVariables"] == {
        "PATH": "/opt/uv/bin:/opt/claude/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    }
    assert data["StandardOutPath"] == f"{logs}/launchd.out"
    assert data["StandardErrorPath"] == f"{logs}/launchd.err"
    assert data["RunAtLoad"] is False
    # launchd는 로그 폴더를 만들지 않으므로 설치 때 있어야 첫 실행이 시작된다.
    assert logs.is_dir()
    assert list(logs.iterdir()) == []


@pytest.mark.parametrize("missing", ["uv", "claude"])
def test_fails_without_uv_or_claude(
    report_launchd: ModuleType, tmp_path: Path, missing: str
) -> None:
    plist = tmp_path / "agent.plist"
    found = {"uv": "/opt/uv/bin/uv", "claude": "/opt/claude/bin/claude"}
    del found[missing]

    reports = tmp_path / "reports"

    code = report_launchd.main(
        ["--plist", str(plist), "--reports-dir", str(reports)],
        which=tools(found),
        repository=tmp_path / "repo",
    )

    assert code == 1
    assert not plist.exists()
    assert not reports.exists()


def test_rejects_reports_inside_repository(
    report_launchd: ModuleType, tmp_path: Path
) -> None:
    plist = tmp_path / "agent.plist"
    repository = tmp_path / "repo"
    reports = repository / "reports"
    which = tools({"uv": "/opt/uv/bin/uv", "claude": "/opt/claude/bin/claude"})

    code = report_launchd.main(
        ["--plist", str(plist), "--reports-dir", str(reports)],
        which=which,
        repository=repository,
    )

    assert code == 1
    assert not plist.exists()
    assert not repository.exists()


def test_allows_reports_next_to_repository(
    report_launchd: ModuleType, tmp_path: Path
) -> None:
    plist = tmp_path / "agent.plist"
    reports = tmp_path / "repo-reports"
    which = tools({"uv": "/opt/uv/bin/uv", "claude": "/opt/claude/bin/claude"})

    code = report_launchd.main(
        ["--plist", str(plist), "--reports-dir", str(reports)],
        which=which,
        repository=tmp_path / "repo",
    )

    assert code == 0
    assert plist.is_file()
    assert (reports / ".logs").is_dir()

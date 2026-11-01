"""수정 직후 hook과 종료 전 hook을 임시 git 저장소와 hook 입력 JSON으로 검사한다.

`uv`는 PATH 앞에 둔 가짜 실행 파일로 바꾼다. 가짜 `uv`는 호출을 기록하고, Ruff는 실제
Ruff로 넘기며, 나머지 검사기는 제어 파일에 적힌 출력과 종료 코드를 흉내 낸다.
"""

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

FAKE_UV = """import json, os, sys, time
from pathlib import Path

args = sys.argv[1:]
assert args[:2] == ["run", "--locked"], args
args = args[2:]
env_file = None
if args[0] == "--env-file":
    env_file, args = args[1], args[2:]
control = Path(os.environ["FAKE_UV_CONTROL"])
with (control / "calls.jsonl").open("a") as log:
    log.write(json.dumps({{"args": args, "env_file": env_file}}) + "\\n")
tool = Path(args[1]).stem if args[0] == "python" else args[0]
spec_path = control / f"{{tool}}.json"
if not spec_path.exists() and tool == "ruff":
    os.execv({ruff!r}, [{ruff!r}, *args[1:]])
spec = json.loads(spec_path.read_text()) if spec_path.exists() else {{}}
for path, content in spec.get("write", {{}}).items():
    Path(path).write_text(content)
time.sleep(spec.get("sleep", 0))
sys.stdout.write(spec.get("stdout", ""))
sys.exit(spec.get("exit", 0))
"""
STYLE_FAILURE = "def flagged(enabled: bool):\n    return enabled\n"
CLEAN = "VALUE = 1\n"
REPOSITORY = Path(__file__).resolve().parents[3]


class Harness:
    """임시 저장소, 가짜 `uv`의 제어 파일과 호출 기록을 다룬다."""

    def __init__(self, root: Path, control: Path) -> None:
        self.root = root
        self.control = control

    def write(self, path: str, content: str) -> Path:
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
        return file

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def commit(self) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")

    def fake(self, tool: str, **spec: Any) -> None:
        (self.control / f"{tool}.json").write_text(json.dumps(spec))

    def calls(self) -> list[dict[str, Any]]:
        log = self.control / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def reset_calls(self) -> None:
        (self.control / "calls.jsonl").unlink(missing_ok=True)

    def tools(self) -> list[str]:
        return [
            Path(call["args"][1]).stem
            if call["args"][0] == "python"
            else call["args"][0]
            for call in self.calls()
        ]

    def pytest_targets(self) -> list[str]:
        calls = [call for call in self.calls() if call["args"][0] == "pytest"]
        assert len(calls) == 1
        assert calls[0]["env_file"] == ".env.example"
        return calls[0]["args"][calls[0]["args"].index("tests/unit") + 1 :]


def git_only_path(tmp_path: Path) -> str:
    """`git`만 있는 PATH 디렉터리. 실제 `uv`가 끼어들지 않게 한다."""
    directory = tmp_path / "git-only"
    if not directory.exists():
        directory.mkdir()
        git = shutil.which("git")
        assert git is not None
        (directory / "git").symlink_to(git)
    return str(directory)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    root = tmp_path / "repo"
    control = tmp_path / "control"
    bin_dir = tmp_path / "bin"
    for directory in (root, control, bin_dir):
        directory.mkdir()
    ruff = shutil.which("ruff", path=str(Path(sys.executable).parent))
    assert ruff is not None
    (control / "fake_uv.py").write_text(FAKE_UV.format(ruff=ruff))
    uv = bin_dir / "uv"
    # 실행 파일 경로에 공백이 있을 수 있어 shebang 대신 셸 스크립트로 넘긴다.
    uv.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{control}/fake_uv.py" "$@"\n')
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{git_only_path(tmp_path)}")
    monkeypatch.setenv("FAKE_UV_CONTROL", str(control))
    monkeypatch.chdir(root)
    harness = Harness(root, control)
    harness.git("init", "-q")
    harness.git("config", "user.email", "hook@example.com")
    harness.git("config", "user.name", "hook")
    shutil.copy(REPOSITORY / "pyproject.toml", root / "pyproject.toml")
    for path in (
        ".env.example",
        "README.md",
        "complexipy-snapshot.json",
        "src/mosemo/database.py",
        "src/mosemo/accounts/x.py",
        "tests/conftest.py",
        "tests/component/conftest.py",
        "tests/component/test_accounts_api.py",
        "tests/component/test_activities_api.py",
        "tests/component/test_auth_api.py",
    ):
        harness.write(path, CLEAN)
    harness.commit()
    return harness


def call(
    hook: ModuleType,
    payload: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = hook.main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def context(output: str) -> str:
    lines = output.splitlines()
    assert len(lines) == 1
    hook_output = json.loads(lines[0])["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PostToolUse"
    return hook_output["additionalContext"]


# AC-1


@pytest.mark.parametrize("tool_name", ["Edit", "Write"])
def test_post_edit_formats_file_and_reports_violations_except_deferred_rules(
    harness: Harness,
    post_edit_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool_name: str,
) -> None:
    file = harness.write(
        "src/mosemo/edited.py",
        "import os\ndef flagged(enabled: bool):\n  unused = 1\n  return enabled\n",
    )
    payload = {"tool_name": tool_name, "tool_input": {"file_path": str(file)}}

    code, out, _ = call(post_edit_hook, payload, monkeypatch, capsys)

    assert code == 0
    assert "\n    return enabled\n" in file.read_text()
    feedback = context(out)
    assert "src/mosemo/edited.py:" in feedback
    assert "FBT001" in feedback
    assert "F401" not in feedback
    assert "F841" not in feedback


def test_post_edit_writes_nothing_without_remaining_violations(
    harness: Harness,
    post_edit_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    file = harness.write("src/mosemo/clean.py", "VALUE  =  1\n")
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(file)}}

    assert call(post_edit_hook, payload, monkeypatch, capsys)[:2] == (0, "")
    assert file.read_text() == CLEAN


# AC-2


def test_post_edit_checks_only_added_updated_and_moved_python_files_of_patch(
    harness: Harness,
    post_edit_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("added", "updated", "moved", "deleted"):
        harness.write(f"src/{name}.py", STYLE_FAILURE)
    harness.write("docs/note.md", "# note\n")
    patch = """*** Begin Patch
*** Add File: src/added.py
+VALUE = 1
*** Update File: src/updated.py
@@
*** Update File: src/original.py
*** Move to: src/moved.py
*** Delete File: src/deleted.py
*** Add File: docs/note.md
*** End Patch
"""
    payload = {
        "tool_name": "apply_patch",
        "cwd": str(harness.root),
        "tool_input": {"command": patch},
    }

    code, out, _ = call(post_edit_hook, payload, monkeypatch, capsys)

    assert code == 0
    checked = [call["args"] for call in harness.calls() if call["args"][1] == "check"]
    assert checked[0][-3:] == ["src/added.py", "src/moved.py", "src/updated.py"]
    feedback = context(out)
    assert "src/added.py" in feedback
    assert "src/deleted.py" not in feedback


@pytest.mark.parametrize(
    "payload",
    [
        {
            "tool_name": "apply_patch",
            "cwd": ".",
            "tool_input": {
                "command": "*** Delete File: src/mosemo/accounts/x.py\n"
                "*** Update File: README.md\n"
            },
        },
        {"tool_name": "Write", "tool_input": {"file_path": "../outside.py"}},
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
    ],
)
def test_post_edit_without_python_target_runs_nothing(
    harness: Harness,
    post_edit_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: dict[str, Any],
) -> None:
    (harness.root.parent / "outside.py").write_text(STYLE_FAILURE)
    assert call(post_edit_hook, payload, monkeypatch, capsys)[:2] == (0, "")
    assert harness.calls() == []


# AC-3


@pytest.mark.parametrize("failure", ["invalid input", "command", "timeout", "no uv"])
def test_post_edit_failure_logs_one_line_and_passes(
    harness: Harness,
    post_edit_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    file = harness.write("src/mosemo/edited.py", STYLE_FAILURE)
    payload: object = {"tool_name": "Edit", "tool_input": {"file_path": str(file)}}
    if failure == "invalid input":
        payload = "{not json"
    elif failure == "command":
        harness.write("src/mosemo/edited.py", "def broken(:\n")
    elif failure == "timeout":
        monkeypatch.setattr(post_edit_hook, "TIME_LIMIT", 0.5)
        harness.fake("ruff", sleep=3)
    else:
        monkeypatch.setenv("PATH", git_only_path(harness.root.parent))

    code, out, err = call(post_edit_hook, payload, monkeypatch, capsys)

    assert (code, out) == (0, "")
    assert len(err.strip().splitlines()) == 1


# AC-4


def test_stop_passes_without_changed_python_paths(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {"session_id": "s"}
    assert call(stop_hook, payload, monkeypatch, capsys) == (0, "", "")
    harness.write("README.md", "changed\n")
    assert call(stop_hook, payload, monkeypatch, capsys) == (0, "", "")
    assert harness.calls() == []


def test_stop_checks_deleted_path_and_skips_recheck_after_pass(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (harness.root / "src/mosemo/accounts/x.py").unlink()
    payload = {"session_id": "s"}

    assert call(stop_hook, payload, monkeypatch, capsys) == (0, "", "")
    assert harness.tools() == [
        "ruff",
        "complexipy",
        "validate_conventions",
        "validate_architecture",
        "ty",
        "pytest",
    ]

    harness.reset_calls()
    assert call(stop_hook, payload, monkeypatch, capsys) == (0, "", "")
    assert harness.calls() == []


# AC-5


def test_stop_formats_shell_edit_and_blocks_style_failure_without_tests(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    file = harness.write(
        "src/mosemo/accounts/x.py", "def flagged(enabled: bool):\n  return enabled\n"
    )

    code, out, err = call(stop_hook, {"session_id": "s"}, monkeypatch, capsys)

    assert (code, out) == (2, "")
    assert file.read_text() == STYLE_FAILURE
    lines = err.splitlines()
    assert lines[0] == stop_hook.INSTRUCTION
    assert "$ uv run --locked ruff check --output-format concise ." in lines
    assert any("FBT001" in line for line in lines)
    assert "pytest" not in harness.tools()


def test_stop_formats_untracked_file_in_new_directory(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    file = harness.write("src/mosemo/fresh/deep/module.py", "VALUE  =  1\n")

    assert call(stop_hook, {"session_id": "s"}, monkeypatch, capsys) == (0, "", "")
    assert file.read_text() == CLEAN
    assert harness.calls()[0]["args"] == [
        "ruff",
        "format",
        "src/mosemo/fresh/deep/module.py",
    ]


def test_stop_limits_each_check_output_to_80_lines(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.write("src/mosemo/accounts/x.py", "VALUE = 2\n")
    harness.fake("ty", exit=1, stdout="".join(f"problem {n}\n" for n in range(100)))

    code, _, err = call(stop_hook, {"session_id": "s"}, monkeypatch, capsys)

    assert code == 2
    assert "problem 79" in err
    assert "problem 80" not in err
    assert "... 20줄 생략" in err


# AC-6


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/mosemo/activities/x.py", ["tests/component/test_activities_api.py"]),
        ("src/mosemo/database.py", ["tests/component"]),
        ("src/mosemo/infra/kafka.py", ["tests/component"]),
        ("src/mosemo/focus_sessions/x.py", []),
        ("tests/component/test_auth_api.py", ["tests/component/test_auth_api.py"]),
        ("tests/integration/x.py", []),
        ("tests/conftest.py", ["tests/component"]),
        ("tests/clock_support.py", ["tests/component"]),
        ("tests/component/conftest.py", ["tests/component"]),
        ("tests/unit/test_x.py", []),
    ],
)
def test_stop_selects_component_tests_by_changed_path(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    path: str,
    expected: list[str],
) -> None:
    harness.write(path, "VALUE = 2\n")

    assert call(stop_hook, {"session_id": "s"}, monkeypatch, capsys) == (0, "", "")
    assert harness.pytest_targets() == expected


def test_stop_selects_component_tests_for_deleted_paths(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (harness.root / "src/mosemo/accounts/x.py").unlink()
    (harness.root / "tests/component/test_auth_api.py").unlink()

    assert call(stop_hook, {"session_id": "s"}, monkeypatch, capsys) == (0, "", "")
    assert harness.pytest_targets() == ["tests/component/test_accounts_api.py"]


@pytest.mark.parametrize("staged", [True, False])
def test_stop_treats_both_sides_of_rename_as_changed(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    staged: bool,
) -> None:
    source = harness.root / "src/mosemo/accounts/x.py"
    target = harness.root / "src/mosemo/activities/x.py"
    target.parent.mkdir(parents=True)
    if staged:
        harness.git("mv", str(source), str(target))
    else:
        source.rename(target)
        harness.git("add", "-N", str(target))
    assert "R" in harness.git("status", "--porcelain")[:2]

    assert call(stop_hook, {"session_id": "s"}, monkeypatch, capsys) == (0, "", "")
    assert harness.pytest_targets() == [
        "tests/component/test_accounts_api.py",
        "tests/component/test_activities_api.py",
    ]


def test_stop_blocks_test_failure(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.write("src/mosemo/accounts/x.py", "VALUE = 2\n")
    harness.fake("pytest", exit=1, stdout="FAILED tests/unit/test_a.py::test_a - x\n")

    code, _, err = call(stop_hook, {"session_id": "s"}, monkeypatch, capsys)

    assert code == 2
    assert "FAILED tests/unit/test_a.py::test_a" in err


# AC-7


def fail_with_shifting_output(harness: Harness, attempt: int, *, check: str) -> None:
    harness.write("src/mosemo/accounts/x.py", f"VALUE = {attempt + 2}\n")
    if check == "ty":
        harness.fake(
            "ty",
            exit=1,
            stdout=f"src/mosemo/accounts/x.py:{attempt + 1}:{attempt + 3}: error bad\n",
        )
    else:
        harness.fake(
            "pytest",
            exit=1,
            stdout="FAILED tests/unit/test_a.py::test_a - assert 1\n"
            f"1 failed in 0.{attempt}1s\n",
        )


@pytest.mark.parametrize("check", ["ty", "pytest"])
def test_stop_passes_on_fourth_block_of_same_failure(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    check: str,
) -> None:
    codes = []
    for attempt in range(4):
        fail_with_shifting_output(harness, attempt, check=check)
        codes.append(call(stop_hook, {"session_id": "s"}, monkeypatch, capsys)[0])

    assert codes == [2, 2, 2, 0]


def test_stop_counts_different_failures_separately(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    codes = []
    for attempt in range(4):
        harness.write("src/mosemo/accounts/x.py", f"VALUE = {attempt + 2}\n")
        harness.fake("ty", exit=1, stdout=f"error {attempt}\n")
        codes.append(call(stop_hook, {"session_id": "s"}, monkeypatch, capsys)[0])

    assert codes == [2, 2, 2, 2]


def test_stop_passes_unchanged_tree_after_block_even_if_checker_rewrites_file(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.write("src/mosemo/accounts/x.py", "VALUE = 2\n")
    harness.fake("complexipy", write={"complexipy-snapshot.json": "[]\n"})
    harness.fake("ty", exit=1, stdout="error\n")
    payload = {"session_id": "s"}

    assert call(stop_hook, payload, monkeypatch, capsys)[0] == 2
    harness.reset_calls()
    assert call(stop_hook, payload, monkeypatch, capsys)[0] == 0
    assert harness.tools() == ["ruff"]


def test_stop_rechecks_after_non_python_change_and_per_session(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.write("src/mosemo/accounts/x.py", "VALUE = 2\n")
    harness.fake("ty", exit=1, stdout="error\n")

    assert call(stop_hook, {"session_id": "a"}, monkeypatch, capsys)[0] == 2
    harness.write(
        "pyproject.toml", (harness.root / "pyproject.toml").read_text() + "\n"
    )
    assert call(stop_hook, {"session_id": "a"}, monkeypatch, capsys)[0] == 2
    harness.write("README.md", "changed\n")
    assert call(stop_hook, {"session_id": "a"}, monkeypatch, capsys)[0] == 2
    harness.write("README.md", "changed again\n")
    assert call(stop_hook, {"session_id": "a"}, monkeypatch, capsys)[0] == 0
    assert call(stop_hook, {"session_id": "b"}, monkeypatch, capsys)[0] == 2


def test_stop_keeps_state_outside_work_tree_and_clears_block_on_pass(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.write("src/mosemo/accounts/x.py", "VALUE = 2\n")
    harness.fake("ty", exit=1, stdout="error\n")
    assert call(stop_hook, {"session_id": "s"}, monkeypatch, capsys)[0] == 2
    state_file = harness.root / ".git" / stop_hook.STATE_FILE
    assert "blocked" in json.loads(state_file.read_text())["s"]
    assert stop_hook.STATE_FILE not in harness.git("status", "--porcelain")

    harness.fake("ty")
    harness.write("src/mosemo/accounts/x.py", "VALUE = 3\n")
    assert call(stop_hook, {"session_id": "s"}, monkeypatch, capsys)[0] == 0
    record = json.loads(state_file.read_text())["s"]
    assert "blocked" not in record
    assert "passed" in record


def test_stop_counts_from_one_when_state_is_unreadable(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (harness.root / ".git" / stop_hook.STATE_FILE).write_text("{broken")
    harness.write("src/mosemo/accounts/x.py", "VALUE = 2\n")
    harness.fake("ty", exit=1, stdout="error\n")

    assert call(stop_hook, {"session_id": "s"}, monkeypatch, capsys)[0] == 2


# AC-8


@pytest.mark.parametrize(
    ("failure", "name", "reason"),
    [
        ("no uv", "ruff format", "명령 없음"),
        ("timeout", "ty check", "시간 초과"),
        ("invalid input", "stop_gate", "예외"),
    ],
)
def test_stop_blocks_hook_failure_and_applies_retry_rule(
    harness: Harness,
    stop_hook: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    name: str,
    reason: str,
) -> None:
    harness.write("src/mosemo/accounts/x.py", "VALUE = 2\n")
    payload: object = {"session_id": "s"}
    if failure == "no uv":
        monkeypatch.setenv("PATH", git_only_path(harness.root.parent))
    elif failure == "timeout":
        monkeypatch.setattr(stop_hook, "TIME_LIMIT", 1.0)
        harness.fake("ty", sleep=3)
    else:
        payload = "{not json"

    code, out, err = call(stop_hook, payload, monkeypatch, capsys)

    assert (code, out) == (2, "")
    assert err.splitlines()[0] == stop_hook.INSTRUCTION
    assert f"검사기 실행 실패: {name} {reason}:" in err
    # 바뀐 것 없이 다시 끝내면 통과하고, 지문을 모르는 입력 오류는 3번까지 막는다.
    codes = [call(stop_hook, payload, monkeypatch, capsys)[0] for _ in range(3)]
    assert codes == ([2, 2, 0] if failure == "invalid input" else [0, 2, 0])


# AC-9


def test_hooks_are_registered_for_claude_code_and_codex() -> None:
    claude = json.loads((REPOSITORY / ".claude/settings.json").read_text())["hooks"]
    codex = json.loads((REPOSITORY / ".codex/hooks.json").read_text())["hooks"]

    for hooks, post_matcher in ((claude, "Edit|Write"), (codex, "^apply_patch$")):
        assert set(hooks) >= {"PostToolUse", "Stop"}
        assert "SubagentStop" not in hooks
        [post] = hooks["PostToolUse"]
        [post_command] = post["hooks"]
        assert post["matcher"] == post_matcher
        assert post_command["timeout"] == 10
        assert "uv run --locked python" in post_command["command"]
        assert ".codex/hooks/post_edit_feedback.py" in post_command["command"]
        [stop] = hooks["Stop"]
        [stop_command] = stop["hooks"]
        assert stop_command["timeout"] == 300
        assert "uv run --locked python" in stop_command["command"]
        assert ".codex/hooks/stop_gate.py" in stop_command["command"]

    assert "$CLAUDE_PROJECT_DIR" in claude["Stop"][0]["hooks"][0]["command"]
    assert "git rev-parse --show-toplevel" in codex["Stop"][0]["hooks"][0]["command"]
    [guard] = codex["PreToolUse"]
    assert guard["matcher"] == "^Bash$"
    assert "protect_main.py" in guard["hooks"][0]["command"]

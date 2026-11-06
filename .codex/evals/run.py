"""Claude Code 평가 실행기.

`uv run --locked python .codex/evals/run.py --task E1 --runs 3 [--harness REV]`

실행마다 base commit의 작업 코드에 평가 대상 하네스 파일을 덮어쓴 새 git 저장소를 임시
디렉터리에 만들고, 부모 세션 환경 변수를 지운 `claude -p`로 작업을 시킨 뒤 판정하고,
결과 한 줄을 `results/`에 남긴다. 사용자 저장소는 읽기만 한다.
"""

import argparse
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / ".codex" / "evals"
RESULTS = EVALS / "results"
MODEL = "claude-opus-5-5"
# `@AGENTS.md` shim. 없는 revision에서는 건너뛴다.
CLAUDE_MD_PATHS = ("CLAUDE.md", "src/mosemo/CLAUDE.md", "tests/CLAUDE.md")
# `.codex/hooks/stop_gate.py`의 `HARNESS_PATHS`에 shim을 더한 목록(harness 테스트가 확인한다).
HARNESS_PATHS = (
    ".codex/",
    ".claude/",
    "AGENTS.md",
    "src/mosemo/AGENTS.md",
    "tests/AGENTS.md",
    "tests/unit/harness/",
    "docs/code-style-rubric.md",
    "docs/adr/",
    *CLAUDE_MD_PATHS,
)
EVALS_PATH = ".codex/evals"
# 평가 세트와 그 harness 테스트는 작업 공간에 두지 않는다(판정 파일을 숨기고 import 실패를 막는다).
EVAL_ONLY_PATHS = (EVALS_PATH, "tests/unit/harness/test_evals.py")
TREE_PATHS = tuple(path.rstrip("/") for path in HARNESS_PATHS)
# 부모 세션이 자식 `claude`에 물려주면 안 되는 변수. 인증·백엔드 설정은 남긴다.
DROPPED_NAMES = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_EFFORT",
        "CLAUDE_PID",
        "CLAUDE_PROJECT_DIR",
        "AI_AGENT",
        "VIRTUAL_ENV",
        # gh가 사용자 인증을 쓰지 못하게 토큰을 지우고 빈 설정 디렉터리를 넣는다.
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GH_CONFIG_DIR",
    }
)
DROPPED_PREFIXES = ("CLAUDE_CODE_", "HERDR_", "MOSEMO_", "GIT_")
KEPT_NAMES = frozenset(
    {
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
        "CLAUDE_CODE_SKIP_VERTEX_AUTH",
        "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
        "CLAUDE_CODE_API_KEY_HELPER_TTL_MS",
    }
)
ALLOWED_TOOLS = (
    "Read",
    "Edit",
    "Write",
    "Grep",
    "Glob",
    "Skill",
    "Agent",
    "TodoWrite",
    "Bash(uv run *)",
    "Bash(uv sync *)",
    "Bash(bash .codex/skills/verification/scripts/validate.sh)",
    "Bash(git status *)",
    "Bash(git diff *)",
    "Bash(git log *)",
    "Bash(git show *)",
    "Bash(ls *)",
)
DISALLOWED_TOOLS = ("Bash(git push *)", "Bash(gh *)")
VALIDATE = ("bash", ".codex/skills/verification/scripts/validate.sh")
PYTEST = ("uv", "run", "--locked", "--env-file", ".env.example", "python", "-m")
PYTEST_ARGS = ("pytest", "-q", "--tb=short", "-p", "no:cacheprovider")
ACCEPTANCE_TEST = "tests/integration/labels/test_eval_acceptance.py"
MUTANT_PLUGIN = "eval_e3_mutant"
E3_TESTS = "tests/unit/observation/"
NO_TESTS_COLLECTED = 5
COLLECTED = re.compile(r"^(\d+)(?:/\d+)? tests? collected", re.MULTILINE)
CLAUDE_TIMEOUT = 3600
DETAIL_LINES = 20


@dataclass(frozen=True)
class Task:
    name: str
    base: str
    budget_usd: float
    prompt: str


@dataclass(frozen=True)
class ClaudeOutcome:
    """`claude -p --output-format json` 결과 중 기록할 값."""

    status: str
    cost_usd: float | None
    turns: int | None
    permission_denials: int | None
    detail: str = ""


@dataclass(frozen=True)
class Judgement:
    acceptance: bool
    validate: bool
    detail: dict[str, Any]


def parse_task(name: str, text: str) -> Task:
    """작업 파일의 `- base:`·`- budget_usd:` 항목과 `## 지시문` 아래 본문을 읽는다."""
    fields = dict(re.findall(r"^- (base|budget_usd): (\S+)$", text, re.MULTILINE))
    _, marker, prompt = text.partition("## 지시문\n")
    if not marker or set(fields) != {"base", "budget_usd"}:
        raise ValueError(f"{name}: base, budget_usd, 「## 지시문」이 필요하다")
    return Task(
        name=name,
        base=fields["base"],
        budget_usd=float(fields["budget_usd"]),
        prompt=prompt.strip() + "\n",
    )


def child_environment(
    environ: Mapping[str, str], extra: Mapping[str, str]
) -> dict[str, str]:
    """부모 세션 변수를 지우고 `extra`를 더한 자식 프로세스 환경."""
    kept = {
        name: value
        for name, value in environ.items()
        if name in KEPT_NAMES
        or (name not in DROPPED_NAMES and not name.startswith(DROPPED_PREFIXES))
    }
    return {**kept, **extra}


def claude_command(task: Task, *, session: str) -> list[str]:
    """지시문은 표준 입력으로 넘긴다."""
    return [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--session-id",
        session,
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        ",".join(ALLOWED_TOOLS),
        "--disallowedTools",
        ",".join(DISALLOWED_TOOLS),
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--model",
        MODEL,
        "--max-budget-usd",
        f"{task.budget_usd:g}",
    ]


def claude_outcome(stdout: str, returncode: int) -> ClaudeOutcome:
    """출력의 `subtype`으로 `completed`·`budget_exceeded`·`error`를 정한다."""
    try:
        result = json.loads(stdout)
    except ValueError:
        result = None
    if not isinstance(result, dict):
        tail = "\n".join(stdout.strip().splitlines()[-DETAIL_LINES:])
        return ClaudeOutcome("error", None, None, None, f"exit {returncode}: {tail}")
    subtype = str(result.get("subtype", ""))
    if "budget" in subtype:
        status = "budget_exceeded"
    elif subtype == "success" and not result.get("is_error"):
        status = "completed"
    else:
        status = "error"
    denials = result.get("permission_denials")
    return ClaudeOutcome(
        status=status,
        cost_usd=result.get("total_cost_usd"),
        turns=result.get("num_turns"),
        permission_denials=len(denials) if isinstance(denials, list) else None,
        detail="" if status == "completed" else subtype,
    )


def test_names(source: str) -> list[str]:
    """모듈의 `test_*` 함수와 `Test*` 클래스의 `test_*` 메서드(`Class::name`)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    names = []
    for node in tree.body:
        if isinstance(node, functions) and node.name.startswith("test"):
            names.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            names += [
                f"{node.name}::{child.name}"
                for child in node.body
                if isinstance(child, functions) and child.name.startswith("test")
            ]
    return names


def added_tests(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    """두 시점의 {경로: 소스}에서 새로 생긴 테스트의 pytest node id."""
    old = {
        f"{path}::{name}" for path, text in before.items() for name in test_names(text)
    }
    return sorted(
        node
        for path, text in after.items()
        for name in test_names(text)
        if (node := f"{path}::{name}") not in old
    )


def collected_count(output: str, returncode: int) -> int | None:
    """`pytest --collect-only -q` 출력의 수집 개수. 수집 0개는 exit 5다."""
    if returncode == NO_TESTS_COLLECTED:
        return 0
    match = COLLECTED.search(output)
    return int(match.group(1)) if match else None


def session_metrics(lines: Iterable[str], session: str) -> list[dict[str, Any]]:
    """지표 로그에서 해당 세션의 줄을 순서대로 고른다."""
    found = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("session") == session:
            found.append(entry)
    return found


def transcript_counts(lines: Iterable[str]) -> dict[str, int]:
    """세션 기록의 assistant 메시지 수(같은 API 메시지 id는 하나)와 도구 호출 수.

    subagent의 기록은 별도 파일이라 세지 않는다.
    """
    messages = set()
    tool_uses = 0
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        messages.add(message.get("id") or entry.get("uuid"))
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        tool_uses += sum(
            isinstance(block, dict) and block.get("type") == "tool_use"
            for block in blocks
        )
    return {"assistant_messages": len(messages), "tool_uses": tool_uses}


def result_line(
    task: Task,
    outcome: ClaudeOutcome,
    *,
    judgement: Judgement | None,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """결과 JSONL 한 줄의 필드. `context`는 실행 번호·세션·revision 등 실행 정보다."""
    return {
        **context,
        "task": task.name,
        "base": task.base,
        "model": MODEL,
        "budget_usd": task.budget_usd,
        "status": outcome.status,
        "status_detail": outcome.detail,
        "cost_usd": outcome.cost_usd,
        "turns": outcome.turns,
        "permission_denials": outcome.permission_denials,
        "acceptance": judgement.acceptance if judgement else None,
        "validate": judgement.validate if judgement else None,
        "judgement": judgement.detail if judgement else {},
    }


def tail(completed: subprocess.CompletedProcess[str]) -> str:
    lines = (completed.stdout + completed.stderr).strip().splitlines()
    return "\n".join(lines[-DETAIL_LINES:])


def execute(
    command: Iterable[str], *, cwd: Path, env: Mapping[str, str], **options: Any
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
        check=False,
        **options,
    )


def checked(
    command: Iterable[str], *, cwd: Path, env: Mapping[str, str], **options: Any
) -> str:
    completed = execute(command, cwd=cwd, env=env, **options)
    if completed.returncode != 0:
        raise RuntimeError(f"{' '.join(completed.args)}: {tail(completed)}")
    return completed.stdout


def git_bytes(arguments: list[str], *, env: Mapping[str, str]) -> bytes:
    completed = subprocess.run(
        ["git", *arguments], cwd=ROOT, env=dict(env), capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(arguments)}: {completed.stderr.decode()}")
    return completed.stdout


def extract(
    revision: str, paths: list[str], *, into: Path, env: Mapping[str, str]
) -> None:
    archive = git_bytes(["archive", "--format=tar", revision, "--", *paths], env=env)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(into, filter="data")


def remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def overlay_harness(
    workspace: Path, *, harness: str | None, env: Mapping[str, str]
) -> None:
    """작업 공간의 하네스 파일을 지우고 평가 대상 revision(없으면 작업 트리)의 것으로 채운다."""
    for path in HARNESS_PATHS:
        remove(workspace / path)
    if harness is not None:
        listed = git_bytes(
            ["ls-tree", "--name-only", harness, "--", *TREE_PATHS], env=env
        )
        present = [
            path
            for path in HARNESS_PATHS
            if path.rstrip("/") in listed.decode().split()
        ]
        extract(harness, present, into=workspace, env=env)
        return
    listed = git_bytes(
        ["ls-files", "-z", "-co", "--exclude-standard", "--", *HARNESS_PATHS], env=env
    )
    for name in filter(None, listed.decode().split("\0")):
        source = ROOT / name
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # `.claude/skills`처럼 git이 링크로 기록한 경로는 링크로 옮긴다.
        if source.is_symlink():
            target.symlink_to(source.readlink())
        elif source.is_file():
            shutil.copy2(source, target)


def harness_revision(
    harness: str | None, *, env: Mapping[str, str]
) -> tuple[str, bool]:
    """하네스 파일의 마지막 커밋(stop_gate와 같은 정의)과 작업 트리 변경 여부."""
    if harness is not None:
        log = ["log", "-1", "--format=%h", harness, "--", *HARNESS_PATHS]
        return git_bytes(log, env=env).decode().strip(), False
    log = ["log", "-1", "--format=%h", "--", *HARNESS_PATHS]
    status = ["status", "--porcelain", "--", *HARNESS_PATHS]
    return (
        git_bytes(log, env=env).decode().strip(),
        bool(git_bytes(status, env=env).strip()),
    )


def build_workspace(
    task: Task, workspace: Path, *, harness: str | None, env: Mapping[str, str]
) -> str:
    """작업 공간을 만들고 첫 커밋의 `src/` 트리 해시를 돌려준다."""
    extract(task.base, ["."], into=workspace, env=env)
    overlay_harness(workspace, harness=harness, env=env)
    for path in EVAL_ONLY_PATHS:
        remove(workspace / path)
    if (workspace / EVALS_PATH).exists():
        raise RuntimeError("작업 공간에 .codex/evals가 남아 있다")
    identity = ("-c", "user.name=mosemo-eval", "-c", "user.email=eval@localhost")
    no_hooks = ("-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false")
    checked(["git", "init", "-q", "--template="], cwd=workspace, env=env)
    checked(["git", "add", "-A"], cwd=workspace, env=env)
    commit = ["git", *identity, *no_hooks, "commit", "-q", "-m", "evaluation base"]
    checked(commit, cwd=workspace, env=env)
    checked(["uv", "sync", "--locked", "--quiet"], cwd=workspace, env=env)
    return checked(["git", "rev-parse", "HEAD:src"], cwd=workspace, env=env).strip()


def run_claude(
    task: Task, workspace: Path, *, session: str, env: Mapping[str, str]
) -> ClaudeOutcome:
    command = claude_command(task, session=session)
    try:
        completed = execute(
            command, cwd=workspace, env=env, input=task.prompt, timeout=CLAUDE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return ClaudeOutcome("error", None, None, None, f"timeout {CLAUDE_TIMEOUT}s")
    return claude_outcome(completed.stdout, completed.returncode)


def label_acceptance(
    task: Task, workspace: Path, *, env: Mapping[str, str]
) -> tuple[bool, dict[str, Any]]:
    shutil.copy2(EVALS / "acceptance" / f"{task.name}.py", workspace / ACCEPTANCE_TEST)
    completed = execute(
        [*PYTEST, *PYTEST_ARGS, ACCEPTANCE_TEST], cwd=workspace, env=env
    )
    return completed.returncode == 0, {"pytest": tail(completed)}


def committed_tests(workspace: Path, *, env: Mapping[str, str]) -> dict[str, str]:
    names = checked(
        ["git", "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", "tests"],
        cwd=workspace,
        env=env,
    ).split("\0")
    return {
        name: checked(["git", "show", f"HEAD:{name}"], cwd=workspace, env=env)
        for name in names
        if PurePosixPath(name).name.startswith("test_") and name.endswith(".py")
    }


def current_tests(workspace: Path) -> dict[str, str]:
    return {
        path.relative_to(workspace).as_posix(): path.read_text(
            encoding="utf-8", errors="replace"
        )
        for path in (workspace / "tests").rglob("test_*.py")
    }


def placement_acceptance(
    workspace: Path, *, env: Mapping[str, str]
) -> tuple[bool, dict[str, Any]]:
    """E3: 새 테스트가 `tests/unit/observation/`에만 있고, 컨테이너 마커가 없고, 변이를 잡는다."""
    added = added_tests(committed_tests(workspace, env=env), current_tests(workspace))
    placed = bool(added) and all(node.startswith(E3_TESTS) for node in added)
    marked = execute(
        [
            *PYTEST,
            *PYTEST_ARGS,
            "-m",
            "requires_db or requires_kafka",
            "--collect-only",
            "tests/unit",
        ],
        cwd=workspace,
        env=env,
    )
    container_tests = collected_count(marked.stdout, marked.returncode)
    killed = None
    if added:
        shutil.copy2(EVALS / "acceptance" / "E3.py", workspace / f"{MUTANT_PLUGIN}.py")
        mutant = execute(
            [*PYTEST, *PYTEST_ARGS, "-p", MUTANT_PLUGIN, *added], cwd=workspace, env=env
        )
        killed = mutant.returncode == 1
    detail = {
        "added": added,
        "container_tests": container_tests,
        "mutant_failed": killed,
    }
    return placed and container_tests == 0 and killed is True, detail


def judge(task: Task, workspace: Path, *, env: Mapping[str, str]) -> Judgement:
    validated = execute(VALIDATE, cwd=workspace, env=env)
    if task.name == "E3":
        passed, detail = placement_acceptance(workspace, env=env)
    else:
        passed, detail = label_acceptance(task, workspace, env=env)
    detail["validate"] = "" if validated.returncode == 0 else tail(validated)
    return Judgement(
        acceptance=passed, validate=validated.returncode == 0, detail=detail
    )


def read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def instruction_conflicts(workspace: Path, *, home: Path) -> list[str]:
    """작업 공간 밖의 `CLAUDE.md`가 프로젝트 지시로 잡히는 조건.

    Claude Code는 프로젝트 계층에 `CLAUDE.md`가 하나라도 있으면 `AGENTS.md`를 읽지 않는다.
    홈 아래에 두면 `~/.claude/CLAUDE.md`가 상위 탐색에 잡힌다. 작업 공간 안의 `CLAUDE.md`는
    하네스 파일 목록에서만 오므로(`overlay_harness`가 base의 것을 지운다) 검사하지 않는다.
    """
    resolved = workspace.resolve()
    problems = []
    if resolved.is_relative_to(home.resolve()):
        problems.append(f"작업 공간이 홈 아래에 있다: {resolved}")
    problems += [
        f"상위 폴더에 CLAUDE.md가 있다: {path}"
        for parent in resolved.parents
        for path in (parent / "CLAUDE.md", parent / ".claude" / "CLAUDE.md")
        if path.exists()
    ]
    return problems


def transcript_lines(session: str, *, env: Mapping[str, str]) -> list[str]:
    """`claude` 설정 디렉터리의 `projects/*/<session>.jsonl`. 없으면 빈 목록이다."""
    config = Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    for path in sorted((config / "projects").glob(f"*/{session}.jsonl")):
        return read_lines(path)
    return []


def gh_authenticated(workspace: Path, *, env: Mapping[str, str]) -> bool:
    """평가 환경에서 `gh auth status`가 성공하는가. `gh`가 없으면 인증 안 됨이다."""
    try:
        completed = execute(
            ["gh", "auth", "status"], cwd=workspace, env=env, timeout=60
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0


def run_once(
    task: Task, *, number: int, harness: str | None, context: dict[str, Any]
) -> dict[str, Any]:
    session = str(uuid.uuid4())
    metrics_log = Path(context["metrics_log"])
    revision, dirty = context["harness_rev"], context["harness_dirty"]
    with (
        tempfile.TemporaryDirectory(prefix="mosemo-eval-") as directory,
        tempfile.TemporaryDirectory(prefix="mosemo-eval-gh-") as gh_config,
    ):
        env = child_environment(
            os.environ,
            {
                "MOSEMO_METRICS_LOG": str(metrics_log),
                "MOSEMO_HARNESS_REV": revision,
                "MOSEMO_HARNESS_DIRTY": str(dirty).lower(),
                "MOSEMO_AGENT": "claude",
                "GH_CONFIG_DIR": gh_config,
            },
        )
        workspace = Path(directory)
        src_tree = build_workspace(task, workspace, harness=harness, env=env)
        conflicts = instruction_conflicts(workspace, home=Path.home())
        if conflicts:
            raise RuntimeError("; ".join(conflicts))
        if gh_authenticated(workspace, env=env):
            raise RuntimeError("평가 환경에서 gh가 인증돼 있다")
        outcome = run_claude(task, workspace, session=session, env=env)
        judgement = judge(task, workspace, env=env)
    run_context = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": number,
        "session": session,
        "claude_version": context["claude_version"],
        "harness_rev": revision,
        "harness_dirty": dirty,
        "src_tree": src_tree,
        "metrics": session_metrics(read_lines(metrics_log), session),
        "gh_authenticated": False,
        **transcript_counts(transcript_lines(session, env=env)),
    }
    return result_line(task, outcome, judgement=judgement, context=run_context)


def metrics_log_path(env: Mapping[str, str]) -> Path:
    common = git_bytes(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"], env=env
    )
    return Path(common.decode().strip()) / "mosemo-agent-metrics.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=["E1", "E2", "E3"])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--harness", help="평가할 하네스 revision. 없으면 작업 트리")
    arguments = parser.parse_args(argv)
    task_file = EVALS / "tasks" / f"{arguments.task}.md"
    task = parse_task(arguments.task, task_file.read_text(encoding="utf-8"))
    env = child_environment(os.environ, {})
    revision, dirty = harness_revision(arguments.harness, env=env)
    version = checked(["claude", "--version"], cwd=ROOT, env=env).strip()
    context = {
        "metrics_log": str(metrics_log_path(env)),
        "harness_rev": revision,
        "harness_dirty": dirty,
        "claude_version": version,
    }
    RESULTS.mkdir(exist_ok=True)
    output = RESULTS / f"{time.strftime('%Y%m%d', time.gmtime())}-{revision}.jsonl"
    for number in range(1, arguments.runs + 1):
        line = run_once(task, number=number, harness=arguments.harness, context=context)
        with output.open("a", encoding="utf-8") as file:
            file.write(json.dumps(line, ensure_ascii=False) + "\n")
        print(
            json.dumps(
                {
                    key: line[key]
                    for key in (
                        "task",
                        "run",
                        "status",
                        "acceptance",
                        "validate",
                        "cost_usd",
                        "turns",
                    )
                }
            )
        )
    print(f"결과: {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Claude Code 평가 실행기.

`uv run --locked python .codex/evals/run.py --task E1 --runs 3 [--harness REV]`

실행마다 base commit의 작업 코드에 평가 대상 하네스 파일을 덮어쓴 새 git 저장소를 임시
디렉터리에 만들고, 부모 세션 환경 변수를 지운 `claude -p`로 작업을 시킨 뒤 판정하고,
결과 한 줄을 `results/`에 남긴다. 사용자 저장소는 읽기만 한다.
"""

import argparse
import ast
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
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
        # git이 사용자 SSH 에이전트로 원격에 닿지 못하게 한다(`git_isolation`).
        "SSH_AUTH_SOCK",
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
JUDGE_TIMEOUT = 1800
# 시간 초과로 그룹을 끝낸 뒤 남은 출력을 기다리는 시간(`setsid`로 빠져나간 자손이 파이프를 잡아도 멈추지 않게).
DRAIN_TIMEOUT = 30
DETAIL_LINES = 20
# Claude Code가 프로젝트 지시로 읽는 상위 폴더 파일. 하나라도 있으면 `AGENTS.md`가 빠진다.
ANCESTOR_INSTRUCTIONS = ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
JUDGE_ERRORS = (OSError, RuntimeError, ValueError, subprocess.SubprocessError)


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


def is_test_file(name: str) -> bool:
    """pytest 기본 `python_files`(`test_*.py`, `*_test.py`)에 맞는가."""
    file = PurePosixPath(name).name
    return file.endswith(".py") and (
        file.startswith("test_") or file.endswith("_test.py")
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


def transcript_counts(lines: Iterable[str]) -> dict[str, int | None]:
    """세션 기록의 assistant 메시지 수(같은 API 메시지 id는 하나)와 도구 호출 수.

    기록이 없으면(빈 입력) 둘 다 `None`이다. subagent의 기록은 별도 파일이라 세지 않는다.
    """
    messages = set()
    tool_uses = 0
    seen = False
    for line in lines:
        seen = True
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
    if not seen:
        return {"assistant_messages": None, "tool_uses": None}
    return {"assistant_messages": len(messages), "tool_uses": tool_uses}


def is_harness_path(name: str) -> bool:
    return any(
        name == path or (path.endswith("/") and name.startswith(path))
        for path in HARNESS_PATHS
    )


def code_tree_hash(listing: str) -> str:
    """`git ls-tree -r HEAD -- src` 출력에서 하네스 파일을 뺀 항목의 해시(작업 코드 동일성)."""
    kept = [
        line
        for line in listing.splitlines()
        if line and not is_harness_path(line.partition("\t")[2])
    ]
    return hashlib.sha256("\n".join(kept).encode()).hexdigest()


def file_signature(path: Path) -> bytes:
    """일반 파일은 내용, 링크는 대상 경로, 그 밖에는 모드. 없으면 `missing`.

    링크를 따라가지 않고, 비차단으로 열어 확인 뒤 FIFO로 바뀐 파일에서도 멈추지 않는다.
    """
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return b"missing"
    if stat.S_ISLNK(mode):
        return f"link {path.readlink()}".encode()
    if not stat.S_ISREG(mode):
        return f"mode {mode:o}".encode()
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return b"changed type"
        return f"{mode:o} ".encode() + hashlib.sha256(file.read()).digest()


def state_digest(files: Iterable[Path]) -> str | None:
    """감시 파일들의 해시. 읽을 수 없으면 `None`이고, 호출자는 바뀐 것으로 본다."""
    digest = hashlib.sha256()
    try:
        for path in sorted(set(files)):
            digest.update(f"{path}\0".encode() + file_signature(path))
    except OSError:
        return None
    return digest.hexdigest()


def watched_files(
    git_dirs: Iterable[Path], *, home: Path, harness_files: Iterable[Path]
) -> list[Path]:
    """에이전트가 바꾸면 사용자 권한으로 코드가 실행되거나 평가가 흔들리는 사용자 파일.

    사용자 저장소 git 디렉터리의 설정·hook, 모든 worktree의 `config.worktree`, 전역 git 설정,
    사용자 작업 트리의 하네스 파일(평가 세트 포함, `harness_files`)이다.
    """
    files = [home / ".gitconfig", home / ".config" / "git" / "config"]
    for directory in git_dirs:
        files += [directory / "config", directory / "config.worktree"]
        files += (directory / "hooks").rglob("*")
        files += (directory / "worktrees").glob("*/config.worktree")
    return [*files, *harness_files]


def worktree_harness_files(env: Mapping[str, str]) -> list[Path]:
    """사용자 작업 트리의 추적·미추적 하네스 파일(무시 파일인 `results/`·캐시 제외)."""
    listed = git_bytes(
        ["ls-files", "-z", "-co", "--exclude-standard", "--", *HARNESS_PATHS], env=env
    )
    return [ROOT / name for name in filter(None, listed.decode().split("\0"))]


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


def stop_group(process: subprocess.Popen[str]) -> None:
    """프로세스 그룹에 남은 자손을 끝낸다. 이미 없으면 아무것도 하지 않는다."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


def execute(
    command: Iterable[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    stdin: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """새 프로세스 그룹으로 실행하고, 끝나거나 시간 초과면 남은 자손까지 끝낸다."""
    arguments = list(command)
    with subprocess.Popen(
        arguments,
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_group(process)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.communicate(timeout=DRAIN_TIMEOUT)
            raise
        finally:
            stop_group(process)
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def checked(command: Iterable[str], *, cwd: Path, env: Mapping[str, str]) -> str:
    completed = execute(command, cwd=cwd, env=env)
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


def resolve_revision(harness: str, *, env: Mapping[str, str]) -> str:
    """`--harness` 값을 커밋 해시로 고정한다. `-`로 시작하는 값도 옵션으로 읽지 않는다."""
    arguments = ["rev-parse", "--verify", "--end-of-options", f"{harness}^{{commit}}"]
    return git_bytes(arguments, env=env).decode().strip()


def harness_revision(
    harness: str | None, *, env: Mapping[str, str]
) -> tuple[str, bool]:
    """하네스 파일 목록의 마지막 커밋과 작업 트리 변경 여부.

    목록은 `stop_gate.py`의 것에 shim을 더한 것이다. 평가 실행에서는 이 값을
    `MOSEMO_HARNESS_REV`로 넘기므로 지표 로그도 같은 값을 쓴다.
    """
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
    """작업 공간을 만들고 첫 커밋 `src/`의 작업 코드 해시(하네스 파일 제외)를 돌려준다."""
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
    listing = checked(
        ["git", "ls-tree", "-r", "HEAD", "--", "src"], cwd=workspace, env=env
    )
    return code_tree_hash(listing)


def run_claude(
    task: Task, workspace: Path, *, session: str, env: Mapping[str, str]
) -> ClaudeOutcome:
    try:
        completed = execute(
            claude_command(task, session=session),
            cwd=workspace,
            env=env,
            stdin=task.prompt,
            timeout=CLAUDE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return ClaudeOutcome("error", None, None, None, f"timeout {CLAUDE_TIMEOUT}s")
    return claude_outcome(completed.stdout, completed.returncode)


def label_acceptance(
    workspace: Path, *, env: Mapping[str, str], acceptance: bytes
) -> tuple[bool, dict[str, Any]]:
    (workspace / ACCEPTANCE_TEST).write_bytes(acceptance)
    completed = execute(
        [*PYTEST, *PYTEST_ARGS, ACCEPTANCE_TEST],
        cwd=workspace,
        env=env,
        timeout=JUDGE_TIMEOUT,
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
        if is_test_file(name)
    }


def current_tests(workspace: Path) -> dict[str, str]:
    return {
        path.relative_to(workspace).as_posix(): path.read_text(
            encoding="utf-8", errors="replace"
        )
        for path in (workspace / "tests").rglob("*.py")
        if is_test_file(path.name)
    }


def placement_acceptance(
    workspace: Path, *, env: Mapping[str, str], acceptance: bytes
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
        timeout=JUDGE_TIMEOUT,
    )
    container_tests = collected_count(marked.stdout, marked.returncode)
    killed = None
    if added:
        (workspace / f"{MUTANT_PLUGIN}.py").write_bytes(acceptance)
        mutant = execute(
            [*PYTEST, *PYTEST_ARGS, "-p", MUTANT_PLUGIN, *added],
            cwd=workspace,
            env=env,
            timeout=JUDGE_TIMEOUT,
        )
        killed = mutant.returncode == 1
    source = checked(
        ["git", "status", "--porcelain", "--", "src"], cwd=workspace, env=env
    )
    detail = {
        "added": added,
        "container_tests": container_tests,
        "mutant_failed": killed,
        # 지시문의 "제품 코드는 바꾸지 않는다". 판정에는 쓰지 않고 기록만 한다.
        "src_changed": bool(source.strip()),
    }
    return placed and container_tests == 0 and killed is True, detail


def judge(
    task: Task, workspace: Path, *, env: Mapping[str, str], acceptance: bytes
) -> Judgement:
    """`acceptance`는 실행 전에 읽어 둔 판정 파일 내용이다(실행 중 변조를 판정에 쓰지 않는다)."""
    validated = execute(VALIDATE, cwd=workspace, env=env, timeout=JUDGE_TIMEOUT)
    if task.name == "E3":
        passed, detail = placement_acceptance(workspace, env=env, acceptance=acceptance)
    else:
        passed, detail = label_acceptance(workspace, env=env, acceptance=acceptance)
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
        f"상위 폴더에 프로젝트 지시 파일이 있다: {path}"
        for parent in resolved.parents
        for path in (parent / name for name in ANCESTOR_INSTRUCTIONS)
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


def remote_reachable(workspace: Path, *, url: str, env: Mapping[str, str]) -> bool:
    """평가 환경에서 사용자 원격에 `git ls-remote`가 성공하는가.

    시간 초과는 차단을 확인하지 못한 것이므로 닿는 것으로 본다.
    """
    try:
        completed = execute(
            ["git", "ls-remote", "--", url], cwd=workspace, env=env, timeout=60
        )
    except subprocess.TimeoutExpired:
        return True
    return completed.returncode == 0


def isolated_environment(config: Path, *, context: Mapping[str, Any]) -> dict[str, str]:
    """`claude -p`와 판정 명령의 환경. `gh`와 git이 사용자 인증을 쓰지 못하게 한다."""
    (config / "gh").mkdir()
    gitconfig = config / "gitconfig"
    gitconfig.touch()
    return child_environment(
        os.environ,
        {
            "MOSEMO_METRICS_LOG": str(context["metrics_log"]),
            "MOSEMO_HARNESS_REV": context["harness_rev"],
            "MOSEMO_HARNESS_DIRTY": str(context["harness_dirty"]).lower(),
            "MOSEMO_AGENT": "claude",
            "GH_CONFIG_DIR": str(config / "gh"),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": "false",
        },
    )


def preflight(workspace: Path, *, env: Mapping[str, str], remote: str | None) -> None:
    """비용을 쓰기 전에 지시 파일 조건과 인증 차단을 단언한다."""
    problems = instruction_conflicts(workspace, home=Path.home())
    if gh_authenticated(workspace, env=env):
        problems.append("평가 환경에서 gh가 인증돼 있다")
    if remote and remote_reachable(workspace, url=remote, env=env):
        problems.append(f"평가 환경에서 git 원격에 닿는다: {remote}")
    if problems:
        raise RuntimeError("; ".join(problems))


def judge_safely(
    task: Task, workspace: Path, *, env: Mapping[str, str], acceptance: bytes
) -> tuple[Judgement | None, str]:
    """판정 실패도 결과 줄에 남기도록 오류를 문자열로 돌려준다."""
    try:
        return judge(task, workspace, env=env, acceptance=acceptance), ""
    except JUDGE_ERRORS as error:
        return None, f"{type(error).__name__}: {error}"


def run_once(
    task: Task, *, number: int, harness: str | None, context: dict[str, Any]
) -> dict[str, Any]:
    session = str(uuid.uuid4())
    git_dirs = [Path(path) for path in context["git_dirs"]]

    def watched() -> list[Path]:
        harness_files = worktree_harness_files(child_environment(os.environ, {}))
        return watched_files(git_dirs, home=Path.home(), harness_files=harness_files)

    with (
        tempfile.TemporaryDirectory(prefix="mosemo-eval-") as directory,
        tempfile.TemporaryDirectory(prefix="mosemo-eval-config-") as config,
    ):
        env = isolated_environment(Path(config), context=context)
        workspace = Path(directory)
        src_tree = build_workspace(task, workspace, harness=harness, env=env)
        preflight(workspace, env=env, remote=context["remote"])
        before = state_digest(watched())
        outcome = run_claude(task, workspace, session=session, env=env)
        judgement, error = judge_safely(
            task, workspace, env=env, acceptance=context["acceptance"]
        )
        # 판정 명령도 에이전트가 쓴 코드를 실행하므로 판정이 끝난 뒤 비교한다.
        after = state_digest(watched())
    run_context = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run": number,
        "session": session,
        "claude_version": context["claude_version"],
        "harness_rev": context["harness_rev"],
        "harness_dirty": context["harness_dirty"],
        "src_tree": src_tree,
        "metrics": session_metrics(read_lines(Path(context["metrics_log"])), session),
        "gh_authenticated": False,
        "git_remote_reachable": False,
        "user_state_changed": before is None or after != before,
        "judgement_error": error,
        **transcript_counts(transcript_lines(session, env=env)),
    }
    return result_line(task, outcome, judgement=judgement, context=run_context)


def user_repository(env: Mapping[str, str]) -> dict[str, Any]:
    """사용자 저장소의 지표 로그 경로, 감시할 git 디렉터리, `origin` URL."""
    common, git_dir = (
        git_bytes(["rev-parse", "--path-format=absolute", flag], env=env)
        .decode()
        .strip()
        for flag in ("--git-common-dir", "--git-dir")
    )
    try:
        remote = git_bytes(["remote", "get-url", "origin"], env=env).decode().strip()
    except RuntimeError:
        remote = None
    return {
        "metrics_log": str(Path(common) / "mosemo-agent-metrics.jsonl"),
        "git_dirs": sorted({common, git_dir}),
        "remote": remote,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=["E1", "E2", "E3"])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--harness", help="평가할 하네스 revision. 없으면 작업 트리")
    arguments = parser.parse_args(argv)
    task_file = EVALS / "tasks" / f"{arguments.task}.md"
    task = parse_task(arguments.task, task_file.read_text(encoding="utf-8"))
    env = child_environment(os.environ, {})
    harness = arguments.harness and resolve_revision(arguments.harness, env=env)
    revision, dirty = harness_revision(harness, env=env)
    version = checked(["claude", "--version"], cwd=ROOT, env=env).strip()
    acceptance = EVALS / "acceptance" / f"{arguments.task}.py"
    context = {
        **user_repository(env),
        "acceptance": acceptance.read_bytes(),
        "harness_rev": revision,
        "harness_dirty": dirty,
        "claude_version": version,
    }
    RESULTS.mkdir(exist_ok=True)
    output = RESULTS / f"{time.strftime('%Y%m%d', time.gmtime())}-{revision}.jsonl"
    for number in range(1, arguments.runs + 1):
        line = run_once(task, number=number, harness=harness, context=context)
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
        if line["user_state_changed"]:
            print(
                "사용자 git 설정·hook 또는 평가 세트가 바뀌었다. 실행을 멈춘다.",
                file=sys.stderr,
            )
            return 1
    print(f"결과: {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

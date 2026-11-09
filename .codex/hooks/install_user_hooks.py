"""사용자 수준 Codex hook 디스패처를 `~/.codex/hooks.json`에 설치하거나 검사한다.

디스패처 명령은 mosemo-server worktree 안에서만 저장소의 `.codex/hooks/` 스크립트를
실행한다. 명령 생성·병합·검사·저장소 수준 건너뛰기 판정은 문자열·dict를 받는 순수
함수이고, 공통 git 디렉터리 계산과 파일 읽기·쓰기만 연결 함수가 한다. Codex 신뢰
(`/hooks`)는 하지 않는다.

    uv run --locked python .codex/hooks/install_user_hooks.py [--check]
        [--hooks-file PATH] [--config-file PATH]
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MARKER = ": mosemo-agent-hook;"
# (이벤트, `hooks.state` 키의 이벤트 이름, matcher, timeout, 스크립트)
DISPATCHERS = (
    ("PostToolUse", "post_tool_use", "^apply_patch$", 10, "post_edit_feedback.py"),
    ("Stop", "stop", None, 300, "stop_gate.py"),
    ("SubagentStop", "subagent_stop", None, 10, "review_metrics.py"),
)
CODEX_DIR = Path.home() / ".codex"
COMMON_DIR = ("rev-parse", "--path-format=absolute", "--git-common-dir")
GIT_TIMEOUT = 2.0


class InstallError(ValueError):
    pass


def dispatcher_command(script: str, common_dir: str) -> str:
    """mosemo-server worktree에서만 `.codex/hooks/SCRIPT`를 실행하는 한 줄 `/bin/sh` 명령.

    git 밖, 다른 공통 git 디렉터리, 스크립트 없는 worktree는 exit 0이다. 실행은 `exec`로
    종료 코드와 출력을 그대로 넘긴다.
    """
    target = f".codex/hooks/{script}"
    return (
        f"{MARKER} "
        "top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0; "
        '[ "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" = '
        f"{shlex.quote(common_dir)} ] || exit 0; "
        f'[ -f "$top"/{shlex.quote(target)} ] || exit 0; '
        'cd "$top" || exit 0; '
        "MOSEMO_AGENT=codex MOSEMO_HOOK_LAYER=user "
        f"exec uv run --locked python {shlex.quote(target)}"
    )


def dispatcher_groups(common_dir: str) -> dict[str, dict[str, Any]]:
    """이벤트별로 설치할 항목 하나."""
    groups = {}
    for event, _, matcher, timeout, script in DISPATCHERS:
        hook = {
            "type": "command",
            "command": dispatcher_command(script, common_dir),
            "timeout": timeout,
        }
        groups[event] = {
            **({"matcher": matcher} if matcher is not None else {}),
            "hooks": [hook],
        }
    return groups


def is_dispatcher(group: Any) -> bool:
    """식별 표시로 시작하는 명령을 가진 항목인가."""
    hooks = group.get("hooks") if isinstance(group, dict) else None
    return any(
        isinstance(hook, dict) and str(hook.get("command", "")).startswith(MARKER)
        for hook in (hooks if isinstance(hooks, list) else [])
    )


def event_table(hooks: Mapping[str, Any]) -> dict[str, Any]:
    table = hooks.get("hooks", {})
    if not isinstance(table, dict):
        raise InstallError("hooks 파일의 `hooks`가 객체가 아니다")
    return table


def merge_hooks(hooks: Mapping[str, Any], common_dir: str) -> dict[str, Any]:
    """식별 표시 항목을 지우고 디스패처 항목을 이벤트 목록 끝에 넣는다.

    다른 항목과 키는 내용과 순서를 그대로 둔다.
    """
    events = {}
    for event, groups in event_table(hooks).items():
        if not isinstance(groups, list):
            raise InstallError(f"hooks 파일의 `{event}`가 목록이 아니다")
        kept = [group for group in groups if not is_dispatcher(group)]
        if kept:
            events[event] = kept
    for event, group in dispatcher_groups(common_dir).items():
        events[event] = [*events.get(event, []), group]
    return {**hooks, "hooks": events}


def serialize(hooks: Mapping[str, Any]) -> str:
    return json.dumps(hooks, ensure_ascii=False, indent=2) + "\n"


def parse_hooks(text: str) -> dict[str, Any]:
    """hooks 파일 내용. 빈 문자열은 파일 없음(빈 설정)이다."""
    if not text.strip():
        return {}
    try:
        hooks = json.loads(text)
    except ValueError as error:
        raise InstallError(f"hooks 파일이 JSON이 아니다: {error}") from error
    if not isinstance(hooks, dict):
        raise InstallError("hooks 파일이 객체가 아니다")
    return hooks


def trust_states(config_text: str) -> dict[str, Any]:
    """`config.toml`의 `hooks.state` 표. 빈 문자열은 파일 없음이다."""
    try:
        config = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as error:
        raise InstallError(f"config 파일이 TOML이 아니다: {error}") from error
    hooks = config.get("hooks")
    states = hooks.get("state") if isinstance(hooks, dict) else None
    return states if isinstance(states, dict) else {}


def install_problems(
    hooks_text: str, config_text: str, *, hooks_path: str, common_dir: str
) -> list[str]:
    """설치·신뢰 상태의 문제. 빈 목록이면 세 항목이 설치 내용과 같고 신뢰 키가 켜져 있다.

    신뢰 해시가 내용과 맞는지는 보지 않는다(Codex 내부 규칙).
    """
    try:
        events = event_table(parse_hooks(hooks_text))
        states = trust_states(config_text)
    except InstallError as error:
        return [str(error)]
    expected = dispatcher_groups(common_dir)
    problems = []
    for event, state_name, *_ in DISPATCHERS:
        groups = events.get(event)
        groups = groups if isinstance(groups, list) else []
        found = [index for index, group in enumerate(groups) if is_dispatcher(group)]
        if len(found) != 1 or groups[found[0]] != expected[event]:
            problems.append(f"{event}: 디스패처 항목이 설치 내용과 다르다")
            continue
        key = f"{hooks_path}:{state_name}:{found[0]}:0"
        state = states.get(key)
        if not isinstance(state, dict):
            problems.append(f"{event}: 신뢰 키 없음: {key}")
        elif state.get("enabled") is False:
            problems.append(f"{event}: 신뢰 키가 꺼져 있다: {key}")
    return problems


def is_repo_layer_codex(environ: Mapping[str, str]) -> bool:
    """저장소 수준 `.codex/hooks.json`으로 불린 Codex 실행인가(`MOSEMO_HOOK_LAYER` 없음)."""
    return environ.get("MOSEMO_AGENT") == "codex" and not environ.get(
        "MOSEMO_HOOK_LAYER"
    )


def repo_hook_skipped(
    environ: Mapping[str, str],
    *,
    hooks_text: str,
    config_text: str,
    hooks_path: str,
    common_dir: str | None,
) -> bool:
    """저장소 수준 `.codex/hooks.json`으로 불린 Codex hook이 바로 끝나야 하는가.

    `environ`은 `MOSEMO_AGENT`·`MOSEMO_HOOK_LAYER`를 읽는 환경 값이다. 사용자 수준
    디스패처가 설치·신뢰돼 같은 스크립트를 실행할 때만 참이다. 이 분기는 저장소 수준
    항목을 지울 때(#231) 함께 지운다.
    """
    if not is_repo_layer_codex(environ) or not common_dir:
        return False
    return not install_problems(
        hooks_text, config_text, hooks_path=hooks_path, common_dir=common_dir
    )


def write_hooks(path: Path, text: str) -> None:
    """`path`에 쓴다. 심볼릭 링크면 링크를 따라간 실제 파일에 쓰고 링크는 남긴다."""
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def read_text(path: Path) -> str:
    """파일 내용. 없으면 빈 문자열이다."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def common_git_dir(directory: Path) -> str | None:
    """`directory`가 속한 저장소의 공통 git 디렉터리. 모르면 `None`이다."""
    try:
        completed = subprocess.run(
            ["git", *COMMON_DIR],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return completed.stdout.strip() or None


def skip_repo_hook() -> bool:
    """저장소 수준 hook 시작 부분의 연결 함수. 판정할 수 없으면 거짓(그대로 실행)이다."""
    if not is_repo_layer_codex(os.environ):
        return False
    hooks_path = CODEX_DIR / "hooks.json"
    try:
        hooks_text = read_text(hooks_path)
        config_text = read_text(CODEX_DIR / "config.toml")
    except OSError, UnicodeDecodeError:
        return False
    return repo_hook_skipped(
        os.environ,
        hooks_text=hooks_text,
        config_text=config_text,
        hooks_path=str(hooks_path),
        common_dir=common_git_dir(Path(__file__).resolve().parent),
    )


def arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hooks-file", default=str(CODEX_DIR / "hooks.json"))
    parser.add_argument("--config-file", default=str(CODEX_DIR / "config.toml"))
    parser.add_argument(
        "--check", action="store_true", help="파일을 바꾸지 않고 설치·신뢰 상태만 본다"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = arguments(argv)
    hooks_path = Path(os.path.abspath(Path(options.hooks_file).expanduser()))
    common_dir = common_git_dir(Path(__file__).resolve().parent)
    if common_dir is None:
        print("공통 git 디렉터리를 찾지 못했다", file=sys.stderr)
        return 1
    try:
        hooks_text = read_text(hooks_path)
        if options.check:
            config_text = read_text(Path(options.config_file).expanduser())
            problems = install_problems(
                hooks_text,
                config_text,
                hooks_path=str(hooks_path),
                common_dir=common_dir,
            )
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1 if problems else 0
        merged = merge_hooks(parse_hooks(hooks_text), common_dir)
        write_hooks(hooks_path, serialize(merged))
    except (OSError, UnicodeDecodeError, InstallError) as error:
        print(f"install_user_hooks: {error}", file=sys.stderr)
        return 1
    print(f"설치함: {hooks_path} (Codex `/hooks`에서 신뢰해야 실행된다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

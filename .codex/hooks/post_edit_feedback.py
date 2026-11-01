"""수정 직후 hook: 바뀐 Python 파일을 포맷하고 남은 Ruff 위반을 모델에 알린다.

Claude Code(`Edit`·`Write`)와 Codex(`apply_patch`)의 `PostToolUse` 입력을 받는다.
막지 않으며 언제나 exit 0이다. 남은 위반이 있을 때만 stdout에 JSON 한 줄을 쓴다.
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

UV_RUN = ("uv", "run", "--locked")
# hook 설정 timeout(10초)보다 먼저 끝내기 위한 내부 제한
TIME_LIMIT = 8.0
# 작성 도중 생기는 지적은 종료 전 hook이 판정한다.
DEFERRED_RULES = "F401,F841"
PATCH_PREFIXES = ("*** Add File: ", "*** Update File: ", "*** Move to: ")


class CommandFailedError(OSError):
    pass


def edited_paths(payload: dict[str, Any]) -> list[Path]:
    """hook 입력에서 수정된 파일 경로를 찾는다. 도구별 차이는 여기서만 다룬다."""
    tool_name = payload["tool_name"]
    tool_input = payload["tool_input"]
    if tool_name in {"Edit", "Write"}:
        return [Path(tool_input["file_path"])]
    if tool_name == "apply_patch":
        base = Path(payload["cwd"])
        return [
            base / line.removeprefix(prefix).strip()
            for line in tool_input["command"].splitlines()
            for prefix in PATCH_PREFIXES
            if line.startswith(prefix)
        ]
    return []


def python_targets(paths: list[Path], root: Path) -> list[str]:
    """저장소 안에 있는 `.py` 파일만 저장소 기준 상대 경로로 고른다."""
    targets = []
    for path in paths:
        resolved = path.resolve()
        if (
            resolved.suffix == ".py"
            and resolved.is_file()
            and resolved.is_relative_to(root)
        ):
            targets.append(resolved.relative_to(root).as_posix())
    return sorted(set(targets))


def run(command: list[str], *, cwd: Path, deadline: float) -> str:
    """명령을 실행하고 출력을 돌려준다. exit 0·1 밖은 명령 실패다."""
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=max(deadline - time.monotonic(), 0.001),
        check=False,
    )
    if completed.returncode not in {0, 1}:
        reason = (completed.stderr or completed.stdout).strip().splitlines()
        raise CommandFailedError(
            f"{command[len(UV_RUN)]} exit {completed.returncode}: "
            f"{reason[0] if reason else ''}"
        )
    return completed.stdout


def repository_root() -> Path:
    output = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=TIME_LIMIT,
        check=True,
    ).stdout
    return Path(output.strip()).resolve()


def feedback(targets: list[str], root: Path) -> str:
    """대상 파일을 포맷하고 남은 위반을 Ruff concise 형식으로 돌려준다."""
    deadline = time.monotonic() + TIME_LIMIT
    run([*UV_RUN, "ruff", "format", "--quiet", *targets], cwd=root, deadline=deadline)
    return run(
        [
            *UV_RUN,
            "ruff",
            "check",
            "--output-format",
            "concise",
            "--quiet",
            "--ignore",
            DEFERRED_RULES,
            *targets,
        ],
        cwd=root,
        deadline=deadline,
    ).strip()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        root = repository_root()
        targets = python_targets(edited_paths(payload), root)
        violations = feedback(targets, root) if targets else ""
    # 수정 직후 hook은 어떤 실패에도 막지 않는다.
    except (
        OSError,
        ValueError,
        LookupError,
        TypeError,
        AttributeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"post_edit_feedback: {type(error).__name__}: {error}", file=sys.stderr)
        return 0
    if violations:
        context = "Ruff 위반이 남아 있다. 저장소 규칙에 맞게 고쳐라.\n" + violations
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": context,
            }
        }
        print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""사용자 수준 Codex hook 설치기(TC-9)와 디스패처 명령(TC-10)과 저장소 hook 등록(TC-19).

셸과 git을 실행하지 않는다(#109 S7). 실제 셸 판정은 소유자 확인(#229)에서 본다.
"""

import copy
import json
import shlex
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
COMMON = "/Users/jolvy/workspace/project/mosemo/mosemo-server/.git"
ODD_COMMON = "/tmp/it's a repo/.git"
HOOKS_PATH = "/Users/jolvy/.codex/hooks.json"
HERDR = {
    "hooks": {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "command": "bash '/Users/jolvy/.codex/herdr-agent-state.sh' session",
                        "timeout": 10,
                        "type": "command",
                    }
                ]
            }
        ]
    }
}
EVENTS = {
    "PostToolUse": "post_tool_use",
    "Stop": "stop",
    "SubagentStop": "subagent_stop",
}


def expected_command(script: str, common: str) -> str:
    return (
        ": mosemo-agent-hook; "
        "top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0; "
        '[ "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" = '
        f"{shlex.quote(common)} ] || exit 0; "
        f'[ -f "$top"/.codex/hooks/{script} ] || exit 0; '
        'cd "$top" || exit 0; '
        "MOSEMO_AGENT=codex MOSEMO_HOOK_LAYER=user "
        f"exec uv run --locked python .codex/hooks/{script}"
    )


def trust_config(*, groups: dict[str, int] | None = None, disabled: str = "") -> str:
    """세 디스패처 항목의 신뢰 키가 있는 `config.toml`. `disabled` 이벤트는 꺼 둔다."""
    groups = groups or {}
    tables = ['[hooks.state."/Users/jolvy/.codex/hooks.json:session_start:0:0"]']
    tables.append('trusted_hash = "sha256:a"')
    for name in EVENTS.values():
        tables.append(f'[hooks.state."{HOOKS_PATH}:{name}:{groups.get(name, 0)}:0"]')
        tables.append('trusted_hash = "sha256:b"')
        if name == disabled:
            tables.append("enabled = false")
    return "[features]\nhooks = true\n\n" + "\n".join(tables) + "\n"


def installed(install_user_hooks: ModuleType, common: str = COMMON) -> str:
    return install_user_hooks.serialize(
        install_user_hooks.merge_hooks(copy.deepcopy(HERDR), common)
    )


@pytest.mark.parametrize(
    "script", ["post_edit_feedback.py", "stop_gate.py", "review_metrics.py"]
)
@pytest.mark.parametrize("common", [COMMON, ODD_COMMON])
def test_dispatcher_command_matches_expected_string_and_tokens(
    install_user_hooks: ModuleType, script: str, common: str
) -> None:
    command = install_user_hooks.dispatcher_command(script, common)

    assert command == expected_command(script, common)
    assert command.startswith(": mosemo-agent-hook;")
    assert shlex.split(command) == [
        ":",
        "mosemo-agent-hook;",
        "top=$(git",
        "rev-parse",
        "--show-toplevel",
        "2>/dev/null)",
        "||",
        "exit",
        "0;",
        "[",
        "$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)",
        "=",
        common,
        "]",
        "||",
        "exit",
        "0;",
        "[",
        "-f",
        f"$top/.codex/hooks/{script}",
        "]",
        "||",
        "exit",
        "0;",
        "cd",
        "$top",
        "||",
        "exit",
        "0;",
        "MOSEMO_AGENT=codex",
        "MOSEMO_HOOK_LAYER=user",
        "exec",
        "uv",
        "run",
        "--locked",
        "python",
        f".codex/hooks/{script}",
    ]


def test_merge_hooks_keeps_other_entries_and_is_idempotent(
    install_user_hooks: ModuleType,
) -> None:
    once = install_user_hooks.merge_hooks(copy.deepcopy(HERDR), COMMON)
    twice = install_user_hooks.merge_hooks(
        json.loads(installed(install_user_hooks)), COMMON
    )

    assert once["hooks"]["SessionStart"] == HERDR["hooks"]["SessionStart"]
    assert list(once["hooks"]) == ["SessionStart", *EVENTS]
    for event in EVENTS:
        assert len(once["hooks"][event]) == 1
    assert once["hooks"]["PostToolUse"][0]["matcher"] == "^apply_patch$"
    assert [once["hooks"][event][0]["hooks"][0]["timeout"] for event in EVENTS] == [
        10,
        300,
        10,
    ]
    assert install_user_hooks.serialize(twice) == installed(install_user_hooks)


def test_merge_hooks_replaces_old_dispatcher_entries(
    install_user_hooks: ModuleType,
) -> None:
    old = json.loads(installed(install_user_hooks, ODD_COMMON))
    old["hooks"]["Stop"].insert(0, {"hooks": [{"type": "command", "command": "x"}]})

    merged = install_user_hooks.merge_hooks(old, COMMON)

    assert merged["hooks"]["Stop"][0] == {
        "hooks": [{"type": "command", "command": "x"}]
    }
    assert len(merged["hooks"]["Stop"]) == 2
    assert COMMON in merged["hooks"]["Stop"][1]["hooks"][0]["command"]
    assert ODD_COMMON not in json.dumps(merged)


def test_merge_hooks_keeps_other_commands_in_dispatcher_group(
    install_user_hooks: ModuleType,
) -> None:
    old = json.loads(installed(install_user_hooks, ODD_COMMON))
    other = {"type": "command", "command": "notify-existing-stop"}
    old["hooks"]["Stop"][0]["hooks"].insert(0, other)
    old["hooks"]["Stop"][0]["statusMessage"] = "kept"
    old["hooks"]["PreToolUse"] = []

    once = install_user_hooks.merge_hooks(old, COMMON)
    twice = install_user_hooks.merge_hooks(copy.deepcopy(once), COMMON)

    assert once["hooks"]["Stop"][0] == {"hooks": [other], "statusMessage": "kept"}
    assert len(once["hooks"]["Stop"]) == 2
    assert once["hooks"]["PreToolUse"] == []
    assert install_user_hooks.serialize(twice) == install_user_hooks.serialize(once)


def test_serialize_uses_two_space_indent_and_final_newline(
    install_user_hooks: ModuleType,
) -> None:
    text = installed(install_user_hooks)

    assert text.endswith("}\n")
    assert text.startswith('{\n  "hooks": {\n    "SessionStart"')


def test_install_problems_none_when_installed_and_trusted(
    install_user_hooks: ModuleType,
) -> None:
    problems = install_user_hooks.install_problems(
        installed(install_user_hooks),
        trust_config(),
        hooks_path=HOOKS_PATH,
        common_dir=COMMON,
    )

    assert problems == []


def test_install_problems_use_entry_position_in_trust_key(
    install_user_hooks: ModuleType,
) -> None:
    hooks = json.loads(installed(install_user_hooks))
    hooks["hooks"]["Stop"].insert(0, {"hooks": [{"type": "command", "command": "x"}]})
    text = install_user_hooks.serialize(hooks)

    shifted = install_user_hooks.install_problems(
        text,
        trust_config(groups={"stop": 1}),
        hooks_path=HOOKS_PATH,
        common_dir=COMMON,
    )
    stale = install_user_hooks.install_problems(
        text, trust_config(), hooks_path=HOOKS_PATH, common_dir=COMMON
    )

    assert shifted == []
    assert stale == [f"Stop: 신뢰 키 없음: {HOOKS_PATH}:stop:1:0"]


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ("", 3),
        ('[hooks.state."/x:stop:0:0"]\n', 3),
        (trust_config(disabled="subagent_stop"), 1),
        ("not = [toml", 1),
    ],
)
def test_install_problems_report_missing_or_disabled_trust(
    install_user_hooks: ModuleType, config: str, expected: int
) -> None:
    problems = install_user_hooks.install_problems(
        installed(install_user_hooks), config, hooks_path=HOOKS_PATH, common_dir=COMMON
    )

    assert len(problems) == expected


def test_install_problems_report_missing_or_changed_entries(
    install_user_hooks: ModuleType,
) -> None:
    other_repo = install_user_hooks.install_problems(
        installed(install_user_hooks, ODD_COMMON),
        trust_config(),
        hooks_path=HOOKS_PATH,
        common_dir=COMMON,
    )
    herdr_only = install_user_hooks.install_problems(
        json.dumps(HERDR), trust_config(), hooks_path=HOOKS_PATH, common_dir=COMMON
    )
    broken = install_user_hooks.install_problems(
        "{", trust_config(), hooks_path=HOOKS_PATH, common_dir=COMMON
    )

    assert len(other_repo) == 3
    assert len(herdr_only) == 3
    assert len(broken) == 1


def test_parse_hooks_rejects_non_object(install_user_hooks: ModuleType) -> None:
    assert install_user_hooks.parse_hooks("") == {}
    with pytest.raises(install_user_hooks.InstallError):
        install_user_hooks.parse_hooks("[]")
    with pytest.raises(install_user_hooks.InstallError):
        install_user_hooks.merge_hooks({"hooks": {"Stop": {}}}, COMMON)


def test_write_hooks_follows_symbolic_link(
    install_user_hooks: ModuleType, tmp_path: Path
) -> None:
    target = tmp_path / "dotfiles" / "hooks.json"
    target.parent.mkdir()
    target.write_text(json.dumps(HERDR), encoding="utf-8")
    link = tmp_path / "codex" / "hooks.json"
    link.parent.mkdir()
    link.symlink_to(target)

    install_user_hooks.write_hooks(link, installed(install_user_hooks))

    assert link.is_symlink()
    assert link.resolve() == target.resolve()
    assert target.read_text(encoding="utf-8") == installed(install_user_hooks)


def test_write_hooks_creates_missing_file(
    install_user_hooks: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "new" / "hooks.json"

    install_user_hooks.write_hooks(path, "{}\n")

    assert path.read_text(encoding="utf-8") == "{}\n"


def test_repository_codex_hooks_keep_only_protect_main() -> None:
    """저장소 `.codex/hooks.json`은 `PreToolUse` 하나만 남기고 `protect_main.py`를 가리킨다(TC-19)."""
    hooks_dir = REPOSITORY / ".codex" / "hooks"
    registered = json.loads((REPOSITORY / ".codex" / "hooks.json").read_text("utf-8"))

    assert list(registered["hooks"]) == ["PreToolUse"]
    (group,) = registered["hooks"]["PreToolUse"]
    assert group["matcher"] == "^Bash$"
    assert "protect_main.py" in group["hooks"][0]["command"]
    for script in ("post_edit_feedback.py", "stop_gate.py"):
        assert "MOSEMO_HOOK_LAYER" not in (hooks_dir / script).read_text("utf-8")

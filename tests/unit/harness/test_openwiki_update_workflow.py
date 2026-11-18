"""`OpenWiki update` 워크플로가 main 병합 후 위키를 갱신해 `openwiki/update` PR로만 올린다."""

import shlex
from pathlib import Path
from typing import Any

import yaml

REPOSITORY = Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY / ".github/workflows/openwiki-update.yml"


def load_workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps_by_name(job_id: str) -> dict[str, dict[str, Any]]:
    job = load_workflow()["jobs"][job_id]
    return {step["name"]: step for step in job["steps"]}


def test_runs_after_main_push_except_generated_pages() -> None:
    workflow = load_workflow()

    # PyYAML은 `on:` 키를 불리언 True로 읽는다.
    assert workflow[True] == {
        "push": {
            "branches": ["main"],
            # 위키 PR 병합이 다시 갱신을 부르지 않고, 지시서 변경은 갱신을 부른다.
            "paths": ["**", "!openwiki/**", "openwiki/INSTRUCTIONS.md"],
        },
        "workflow_dispatch": None,
    }
    assert workflow["permissions"] == {"contents": "read"}
    # 진행 중인 갱신을 끊으면 OpenWiki 페이지 큐가 중간에 버려진다.
    assert workflow["concurrency"] == {
        "group": "openwiki-update",
        "cancel-in-progress": False,
    }
    # 수동 실행도 main에서만 생성한다.
    assert workflow["jobs"]["generate"]["if"] == "github.ref == 'refs/heads/main'"
    assert workflow["jobs"]["publish"]["needs"] == "generate"


def test_generate_job_has_only_claude_token_and_writes_only_openwiki() -> None:
    steps = steps_by_name("generate")
    claude = steps["Update wiki with Claude Code"]

    for step in steps.values():
        assert "OPENWIKI_PR_TOKEN" not in str(step)
    assert claude["env"] == {
        "CLAUDE_CODE_OAUTH_TOKEN": "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    }
    assert claude["run"].startswith('claude -p "/openwiki ')
    args = shlex.split(claude["run"])
    tools = args[args.index("--allowedTools") + 1].split(",")
    assert "Edit(openwiki/**)" in tools
    assert not {"Edit", "Write", "Bash"} & set(tools)
    upload = steps["Upload generated wiki"]
    assert upload["with"]["path"] == "openwiki/"
    assert upload["with"]["include-hidden-files"] is True


def test_publish_job_verifies_generated_wiki_on_latest_main() -> None:
    steps = steps_by_name("publish")

    assert steps["Check out main"]["with"] == {
        "ref": "main",
        "persist-credentials": False,
    }
    assert steps["Verify and apply generated wiki"]["run"].startswith(
        "python3 .codex/skills/verification/scripts/sync_openwiki_update.py "
    )


def test_publishes_only_generated_pages_to_update_branch() -> None:
    step = steps_by_name("publish")["Open docs PR with auto-merge"]
    lines = [line.strip() for line in step["run"].splitlines()]

    # 기본 GITHUB_TOKEN으로 만든 PR은 필수 검사를 실행하지 않는다.
    assert step["env"] == {"GH_TOKEN": "${{ secrets.OPENWIKI_PR_TOKEN }}"}
    assert "git add -- openwiki" in lines
    assert "git switch -C openwiki/update" in lines
    assert "git push --force origin openwiki/update" in lines
    assert "gh pr merge openwiki/update --auto --squash" in lines

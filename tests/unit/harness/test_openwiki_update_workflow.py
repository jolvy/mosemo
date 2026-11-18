"""`OpenWiki update` 워크플로가 main 병합 후 위키를 갱신해 `openwiki/update` PR로만 올린다."""

from pathlib import Path
from typing import Any

import yaml

REPOSITORY = Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY / ".github/workflows/openwiki-update.yml"


def load_workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps_by_name() -> dict[str, dict[str, Any]]:
    [job] = load_workflow()["jobs"].values()
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


def test_checkout_keeps_history_without_persisted_credentials() -> None:
    checkout = steps_by_name()["Check out repository"]

    assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}


def test_updates_wiki_with_claude_subscription_token() -> None:
    step = steps_by_name()["Update wiki with Claude Code"]

    assert step["env"] == {
        "CLAUDE_CODE_OAUTH_TOKEN": "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    }
    assert step["run"].startswith('claude -p "/openwiki ')


def test_publishes_only_generated_pages_to_update_branch() -> None:
    step = steps_by_name()["Open docs PR with auto-merge"]
    lines = [line.strip() for line in step["run"].splitlines()]

    # 기본 GITHUB_TOKEN으로 만든 PR은 필수 검사를 실행하지 않는다.
    assert step["env"] == {"GH_TOKEN": "${{ secrets.OPENWIKI_PR_TOKEN }}"}
    assert "git add -- openwiki" in lines
    assert "git switch -C openwiki/update" in lines
    assert "gh pr merge openwiki/update --auto --squash" in lines

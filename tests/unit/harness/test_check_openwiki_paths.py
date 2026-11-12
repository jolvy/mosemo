"""`check_openwiki_paths.py`가 일반 PR의 `openwiki/` 생성물 변경을 거부하고 CI가 이를 실행한다."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / ".codex/skills/verification/scripts/check_openwiki_paths.py"
CI = REPOSITORY / ".github/workflows/ci.yml"
PAGE = "openwiki/quickstart.md"
INSTRUCTIONS = "openwiki/INSTRUCTIONS.md"
# git hook이 넘기는 GIT_DIR·GIT_INDEX_FILE 등이 임시 저장소 대신 이 저장소를 가리키지
# 않게 상속한 GIT_* 변수를 지운다. CI 러너에 git 사용자 설정이 없어도 commit할 수 있게
# 신원을 직접 준다.
GIT_ENV = {
    **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, check=True)


def commit(repo: Path) -> None:
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "--allow-empty", "-m", "change")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "--quiet")
    for path in (PAGE, INSTRUCTIONS, "README.md"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(f"{path}\n", encoding="utf-8")
    commit(tmp_path)
    return tmp_path


def check(
    repo: Path, head_ref: str = "feat/x", same_repo: str = "true"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--base",
            "HEAD^1",
            "--head",
            "HEAD",
            "--head-ref",
            head_ref,
            "--same-repo",
            same_repo,
        ],
        cwd=repo,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )


def test_rejects_generated_page_edit_on_regular_branch(repo: Path) -> None:
    (repo / PAGE).write_text("edited\n", encoding="utf-8")
    commit(repo)

    result = check(repo)

    assert result.returncode == 1
    assert PAGE in result.stdout.splitlines()


def test_rejects_generated_page_deletion(repo: Path) -> None:
    (repo / PAGE).unlink()
    commit(repo)

    result = check(repo)

    assert result.returncode == 1
    assert PAGE in result.stdout.splitlines()


def test_rejects_moving_generated_page_out_of_openwiki(repo: Path) -> None:
    git(repo, "mv", PAGE, "docs-quickstart.md")
    commit(repo)

    result = check(repo)

    assert result.returncode == 1
    assert PAGE in result.stdout.splitlines()


def test_rejects_generated_path_with_non_ascii_name(repo: Path) -> None:
    page = "openwiki/한글.md"
    (repo / page).write_text("new\n", encoding="utf-8")
    commit(repo)

    result = check(repo)

    assert result.returncode == 1
    assert page in result.stdout.splitlines()


def test_allows_instructions_edit(repo: Path) -> None:
    (repo / INSTRUCTIONS).write_text("edited\n", encoding="utf-8")
    commit(repo)

    assert check(repo).returncode == 0


def test_allows_generated_page_edit_on_same_repo_update_branch(repo: Path) -> None:
    (repo / PAGE).write_text("edited\n", encoding="utf-8")
    commit(repo)

    assert check(repo, head_ref="openwiki/update").returncode == 0


def test_rejects_generated_page_edit_on_fork_update_branch(repo: Path) -> None:
    (repo / PAGE).write_text("edited\n", encoding="utf-8")
    commit(repo)

    result = check(repo, head_ref="openwiki/update", same_repo="false")

    assert result.returncode == 1
    assert PAGE in result.stdout.splitlines()


def test_allows_change_outside_openwiki(repo: Path) -> None:
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    commit(repo)

    assert check(repo).returncode == 0


def quality_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return workflow["jobs"]["quality"]["steps"]


def test_quality_checkout_fetches_merge_commit_parent() -> None:
    checkout = next(
        step
        for step in quality_steps()
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )

    assert checkout["with"]["fetch-depth"] == 2


def test_quality_runs_check_only_on_pull_request_with_pr_inputs() -> None:
    step = next(
        step
        for step in quality_steps()
        if "check_openwiki_paths.py" in str(step.get("run", ""))
    )

    assert step["if"] == "github.event_name == 'pull_request'"
    assert step["env"] == {
        "HEAD_REF": "${{ github.head_ref }}",
        "SAME_REPO": (
            "${{ github.event.pull_request.head.repo.full_name == github.repository }}"
        ),
    }
    assert '--head-ref "$HEAD_REF"' in step["run"]
    assert '--same-repo "$SAME_REPO"' in step["run"]

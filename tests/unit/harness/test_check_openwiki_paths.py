"""`check_openwiki_paths.py`가 일반 PR의 `openwiki/` 생성물 변경을 거부하고 `OpenWiki paths` 워크플로가 이를 실행한다."""

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
OPENWIKI_PATHS = REPOSITORY / ".github/workflows/openwiki-paths.yml"
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


@pytest.mark.parametrize(
    "page",
    # 지시서 예외는 정확한 경로만 허용한다. 대소문자가 다르면 macOS에서 지시서를 덮어쓴다.
    ["OpenWiki/quickstart.md", "OpenWiki/INSTRUCTIONS.md"],
)
def test_rejects_generated_path_with_different_case(repo: Path, page: str) -> None:
    # 대소문자 무시 파일 시스템에서도 별도 경로가 되도록 작업 트리 없이 index에 넣는다.
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        env=GIT_ENV,
        input="new\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{page}")
    git(repo, "commit", "--quiet", "-m", "change")

    result = check(repo)

    assert result.returncode == 1
    assert page in result.stdout.splitlines()


def test_default_commits_compare_merge_commit_with_first_parent(repo: Path) -> None:
    git(repo, "branch", "--quiet", "-M", "main")
    git(repo, "switch", "--quiet", "-c", "feat/x")
    (repo / PAGE).write_text("edited\n", encoding="utf-8")
    commit(repo)
    git(repo, "switch", "--quiet", "main")
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    commit(repo)
    git(repo, "merge", "--quiet", "--no-ff", "-m", "merge", "feat/x")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--head-ref", "feat/x", "--same-repo", "true"],
        cwd=repo,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout.splitlines()[1:] == [PAGE]


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


def test_rejects_abbreviated_options(repo: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--bas", "HEAD", "--head-ref", "feat/x"]
        + ["--same-repo", "true"],
        cwd=repo,
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2


def load_workflow(path: Path) -> dict[Any, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_openwiki_paths_workflow_runs_base_check_on_pull_request_target() -> None:
    workflow = load_workflow(OPENWIKI_PATHS)

    # PyYAML은 `on:` 키를 불리언 True로 읽는다.
    assert workflow[True] == {
        "pull_request_target": {
            "types": ["opened", "synchronize", "reopened", "edited"]
        }
    }
    assert workflow["permissions"] == {"contents": "read"}
    [job] = workflow["jobs"].values()
    assert job["name"] == "OpenWiki paths"
    # skipped job·step은 필수 검사를 통과한 것으로 처리되므로 조건을 두지 않는다.
    for item in [job, *job["steps"]]:
        assert "permissions" not in item
        assert "if" not in item

    [checkout] = [
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    # `ref`가 없으므로 PR head가 아니라 base를 checkout한다.
    assert checkout["with"] == {"persist-credentials": False, "fetch-depth": 0}

    [step] = [step for step in job["steps"] if "run" in step]
    assert step["run"].splitlines() == [
        'git fetch --no-tags origin "+refs/pull/${PR_NUMBER}/head:refs/remotes/pr/head"',
        'base=$(git merge-base "$BASE_SHA" "$HEAD_SHA")',
        (
            "python3 .codex/skills/verification/scripts/check_openwiki_paths.py"
            ' --base "$base" --head "$HEAD_SHA" --head-ref "$HEAD_REF"'
            ' --same-repo "$SAME_REPO"'
        ),
    ]
    assert step["env"] == {
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
        "HEAD_REF": "${{ github.head_ref }}",
        "SAME_REPO": (
            "${{ github.event.pull_request.head.repo.full_name == github.repository }}"
        ),
        "PR_NUMBER": "${{ github.event.pull_request.number }}",
    }


def test_quality_job_no_longer_runs_openwiki_check() -> None:
    steps = load_workflow(CI)["jobs"]["quality"]["steps"]

    assert "Protect generated OpenWiki pages" not in [
        step.get("name") for step in steps
    ]
    checkout = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert "fetch-depth" not in checkout.get("with", {})

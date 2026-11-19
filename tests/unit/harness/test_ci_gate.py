"""`ci_gate.py`가 openwiki만 바뀐 PR을 판정하고 필수 job 결과를 모으며 `CI` 워크플로가 이를 실행한다."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY / ".codex/skills/verification/scripts/ci_gate.py"
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


def run(
    cwd: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env=GIT_ENV if env is None else env,
        capture_output=True,
        text=True,
        check=False,
    )


def changes(repo: Path, event: str = "pull_request", base_ref: str = "main") -> str:
    result = run(repo, "changes", "--event", event, "--base-ref", base_ref)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_openwiki_only_change_is_true(repo: Path) -> None:
    (repo / PAGE).write_text("edited\n", encoding="utf-8")
    commit(repo)

    assert changes(repo) == "openwiki_only=true\n"


def test_instructions_only_change_is_true(repo: Path) -> None:
    (repo / INSTRUCTIONS).write_text("edited\n", encoding="utf-8")
    commit(repo)

    assert changes(repo) == "openwiki_only=true\n"


def test_non_ascii_openwiki_page_is_true(repo: Path) -> None:
    (repo / "openwiki/한글.md").write_text("new\n", encoding="utf-8")
    commit(repo)

    assert changes(repo) == "openwiki_only=true\n"


def test_change_inside_and_outside_openwiki_is_false(repo: Path) -> None:
    (repo / PAGE).write_text("edited\n", encoding="utf-8")
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    commit(repo)

    assert changes(repo) == "openwiki_only=false\n"


def test_moving_file_into_openwiki_is_false(repo: Path) -> None:
    # `--no-renames`가 없으면 새 경로만 보여 `openwiki/` 안의 변경으로 판정된다.
    git(repo, "mv", "README.md", "openwiki/README.md")
    commit(repo)

    assert changes(repo) == "openwiki_only=false\n"


def test_merge_commit_compares_with_first_parent(repo: Path) -> None:
    git(repo, "branch", "--quiet", "-M", "main")
    git(repo, "switch", "--quiet", "-c", "openwiki/update")
    (repo / PAGE).write_text("edited\n", encoding="utf-8")
    commit(repo)
    git(repo, "switch", "--quiet", "main")
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    commit(repo)
    git(repo, "merge", "--quiet", "--no-ff", "-m", "merge", "openwiki/update")

    assert changes(repo) == "openwiki_only=true\n"


def test_different_case_openwiki_is_false(repo: Path) -> None:
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
    git(repo, "update-index", "--add", "--cacheinfo", "100644", blob, "OpenWiki/x.md")
    git(repo, "commit", "--quiet", "-m", "change")

    assert changes(repo) == "openwiki_only=false\n"


def test_empty_commit_is_false(repo: Path) -> None:
    commit(repo)

    assert changes(repo) == "openwiki_only=false\n"


def test_push_event_is_false(repo: Path) -> None:
    (repo / PAGE).write_text("edited\n", encoding="utf-8")
    commit(repo)

    assert changes(repo, event="push", base_ref="") == "openwiki_only=false\n"


def test_pull_request_to_other_base_is_false(repo: Path) -> None:
    # base만 main으로 바꾸면 CI가 다시 돌지 않으므로 다른 base 기준으로 건너뛰지 않는다.
    (repo / PAGE).write_text("edited\n", encoding="utf-8")
    commit(repo)

    assert changes(repo, base_ref="feat/a") == "openwiki_only=false\n"


def test_fails_outside_git_repository(tmp_path: Path) -> None:
    # 상위 폴더의 저장소를 찾지 않게 한다.
    env = {**GIT_ENV, "GIT_CEILING_DIRECTORIES": str(tmp_path.parent)}

    result = run(
        tmp_path, "changes", "--event", "pull_request", "--base-ref", "main", env=env
    )

    assert result.returncode != 0
    assert "openwiki_only" not in result.stdout


def test_rejects_abbreviated_options(repo: Path) -> None:
    result = run(repo, "changes", "--ev", "pull_request", "--base-ref", "main")

    assert result.returncode != 0
    assert "openwiki_only" not in result.stdout


def job(result: str, **outputs: str) -> dict[str, Any]:
    return {"result": result, "outputs": outputs}


def verdict(needs_json: str | None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in GIT_ENV.items() if key != "NEEDS_JSON"}
    if needs_json is not None:
        env["NEEDS_JSON"] = needs_json
    return run(REPOSITORY, "verdict", env=env)


def needs(
    *, changes: str = "success", openwiki_only: str, static: str, tests: str
) -> str:
    return json.dumps(
        {
            "changes": job(changes, openwiki_only=openwiki_only),
            "static": job(static),
            "tests": job(tests),
        }
    )


def test_verdict_passes_when_all_jobs_succeed() -> None:
    result = verdict(needs(openwiki_only="false", static="success", tests="success"))

    assert result.returncode == 0


def test_verdict_passes_when_tests_skipped_for_openwiki_only() -> None:
    result = verdict(needs(openwiki_only="true", static="success", tests="skipped"))

    assert result.returncode == 0


@pytest.mark.parametrize(
    ("kwargs", "failed"),
    [
        (
            {"openwiki_only": "true", "static": "skipped", "tests": "skipped"},
            "static: skipped",
        ),
        (
            {"openwiki_only": "false", "static": "success", "tests": "skipped"},
            "tests: skipped",
        ),
        (
            {"openwiki_only": "false", "static": "failure", "tests": "success"},
            "static: failure",
        ),
        (
            {"openwiki_only": "false", "static": "cancelled", "tests": "success"},
            "static: cancelled",
        ),
        (
            {"openwiki_only": "false", "static": "success", "tests": "failure"},
            "tests: failure",
        ),
        (
            {"openwiki_only": "false", "static": "success", "tests": "cancelled"},
            "tests: cancelled",
        ),
        (
            {
                "changes": "failure",
                "openwiki_only": "",
                "static": "success",
                "tests": "skipped",
            },
            "changes: failure",
        ),
    ],
)
def test_verdict_reports_failed_job(kwargs: dict[str, str], failed: str) -> None:
    result = verdict(needs(**kwargs))

    assert result.returncode == 1
    assert failed in result.stdout.splitlines()


@pytest.mark.parametrize(
    "needs_json",
    [
        None,
        "not json",
        json.dumps({"changes": job("success", openwiki_only="false")}),
        json.dumps({"static": job("success"), "tests": job("success")}),
        json.dumps(
            {
                "changes": {"result": "success", "outputs": ["openwiki_only"]},
                "static": job("success"),
                "tests": job("success"),
            }
        ),
    ],
)
def test_verdict_rejects_missing_or_malformed_needs(needs_json: str | None) -> None:
    result = verdict(needs_json)

    assert result.returncode == 1
    # 처리하지 못한 예외도 1로 끝나므로 안내 문구로 정상 거절을 구분한다.
    assert result.stdout.startswith("NEEDS_JSON")
    assert "Traceback" not in result.stderr


def load_workflow() -> dict[Any, Any]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def step_running(job: dict[str, Any], command: str) -> dict[str, Any]:
    [step] = [step for step in job["steps"] if command in step.get("run", "")]
    return step


def checkout(job: dict[str, Any]) -> dict[str, Any]:
    [step] = [
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    return step


def test_only_pull_request_runs_cancel_previous_run() -> None:
    # main push는 뒤따르는 OpenWiki 자동 병합 push에 취소되지 않고 끝까지 돌아야 한다.
    assert load_workflow()["concurrency"] == {
        "group": "ci-${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }


def test_ci_jobs_split_static_checks_and_tests() -> None:
    jobs = load_workflow()["jobs"]
    skip_tests = "needs.changes.outputs.openwiki_only != 'true'"

    assert {
        job_id: (job["name"], job.get("needs"), job.get("if"))
        for job_id, job in jobs.items()
    } == {
        "changes": ("Detect changes", None, None),
        "static": ("Static checks", None, None),
        "tests": ("Tests", "changes", skip_tests),
        "fault-injection": ("Fault injection tests", "changes", skip_tests),
        "e2e": ("E2E tests", "changes", skip_tests),
        "quality": ("Quality", ["changes", "static", "tests"], "always()"),
    }


def test_changes_job_detects_openwiki_only_changes() -> None:
    job = load_workflow()["jobs"]["changes"]

    assert checkout(job)["with"] == {"fetch-depth": 2}
    step = step_running(job, "ci_gate.py")
    assert step["run"] == (
        "python3 .codex/skills/verification/scripts/ci_gate.py changes"
        ' --event "$EVENT_NAME" --base-ref "$BASE_REF" >> "$GITHUB_OUTPUT"'
    )
    assert step["env"] == {
        "EVENT_NAME": "${{ github.event_name }}",
        "BASE_REF": "${{ github.base_ref }}",
    }
    assert job["outputs"] == {
        "openwiki_only": f"${{{{ steps.{step['id']}.outputs.openwiki_only }}}}"
    }


def test_quality_job_only_runs_verdict() -> None:
    job = load_workflow()["jobs"]["quality"]

    assert "with" not in checkout(job)
    [check] = [step for step in job["steps"] if "run" in step]
    assert (
        check["run"] == "python3 .codex/skills/verification/scripts/ci_gate.py verdict"
    )
    assert check["env"] == {"NEEDS_JSON": "${{ toJSON(needs) }}"}

"""CI에서 테스트를 건너뛸지 정하고 필수 검사 `Quality`의 결과를 모은다.

`changes`는 PR이 `openwiki/` 아래만 바꿨는지 판정한다. 변경 파일은
`check_openwiki_paths.py`와 같은 이유로 `git diff --name-only --no-renames -z
HEAD^1 HEAD`로 구한다. `HEAD^1`·`HEAD`는 pull_request 이벤트의 merge commit
기준이다. 판정이 틀리면 건너뛰기보다 전부 실행하는 쪽이 안전하므로 대소문자를
구분하고, pull_request가 아니거나 변경 경로가 없으면 `false`다. base가 `main`이
아니어도 `false`다. base만 바꾸면 CI가 다시 돌지 않으므로, 다른 브랜치 기준으로
건너뛴 결과가 main 병합에 쓰이지 않게 한다.

`verdict`는 GitHub `needs` 컨텍스트의 JSON(`NEEDS_JSON`)을 읽어 `changes`·`static`이
성공했고 `tests`가 성공했거나 openwiki만 바뀌어 건너뛰었는지 확인한다.

러너 기본 `python3`로 실행하므로 표준 라이브러리와 Python 3.12 문법만 쓴다.
"""

import argparse
import json
import os
import subprocess
import sys

OPENWIKI_PREFIX = "openwiki/"
SKIP_BASE_REF = "main"
REQUIRED_JOBS = ("changes", "static", "tests")


class InvalidNeedsError(ValueError):
    """`NEEDS_JSON`이 GitHub `needs` 컨텍스트 형식이 아니다."""


def changed_paths() -> list[str]:
    output = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", "HEAD^1", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [path for path in output.split("\0") if path]


def openwiki_only(event: str, base_ref: str) -> bool:
    if event != "pull_request" or base_ref != SKIP_BASE_REF:
        return False
    paths = changed_paths()
    return bool(paths) and all(path.startswith(OPENWIKI_PREFIX) for path in paths)


def failed_jobs(needs: object) -> list[str]:
    """어긴 job을 `job: result`로 돌려준다."""
    if not isinstance(needs, dict):
        raise InvalidNeedsError("NEEDS_JSON이 객체가 아니다")
    results: dict[str, object] = {}
    for name in REQUIRED_JOBS:
        job = needs.get(name)
        if not isinstance(job, dict):
            raise InvalidNeedsError(f"NEEDS_JSON에 {name} job이 없다")
        results[name] = job.get("result")
    outputs = needs["changes"].get("outputs") or {}
    if not isinstance(outputs, dict):
        raise InvalidNeedsError("NEEDS_JSON의 changes outputs가 객체가 아니다")
    tests_skipped_for_openwiki = (
        results["tests"] == "skipped" and outputs.get("openwiki_only") == "true"
    )
    return [
        f"{name}: {result}"
        for name, result in results.items()
        if result != "success" and not (name == "tests" and tests_skipped_for_openwiki)
    ]


def run_changes(event: str, base_ref: str) -> int:
    try:
        only = openwiki_only(event, base_ref)
    except subprocess.CalledProcessError as error:
        print(error.stderr, end="", file=sys.stderr)
        print("변경 경로를 구하지 못했다", file=sys.stderr)
        return 1
    print(f"openwiki_only={'true' if only else 'false'}")
    return 0


def run_verdict() -> int:
    raw = os.environ.get("NEEDS_JSON")
    if raw is None:
        print("NEEDS_JSON 환경 변수가 없다")
        return 1
    try:
        failed = failed_jobs(json.loads(raw))
    except ValueError as error:
        # json.JSONDecodeError도 ValueError다.
        print(f"NEEDS_JSON을 읽지 못했다: {error}")
        return 1
    if failed:
        print("필수 job이 통과하지 못했다:")
        print("\n".join(failed))
        return 1
    print("필수 job 통과")
    return 0


def main(argv: list[str] | None = None) -> int:
    # 축약 옵션(`--ev`)이 CI 배선 테스트의 단언을 피하지 못하게 한다.
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    changes = commands.add_parser(
        "changes", help="openwiki_only 출력", allow_abbrev=False
    )
    changes.add_argument("--event", required=True, help="GitHub 이벤트 이름")
    changes.add_argument(
        "--base-ref", required=True, help="PR base 브랜치 이름(PR이 아니면 빈 값)"
    )
    commands.add_parser("verdict", help="필수 job 결과 집계", allow_abbrev=False)
    args = parser.parse_args(argv)
    if args.command == "changes":
        return run_changes(args.event, args.base_ref)
    return run_verdict()


if __name__ == "__main__":
    raise SystemExit(main())

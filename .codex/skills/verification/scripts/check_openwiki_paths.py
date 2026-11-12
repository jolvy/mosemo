"""일반 PR이 `openwiki/` 생성물을 바꾸지 못하게 검사한다.

같은 저장소의 `openwiki/update` 브랜치 PR만 `openwiki/` 아래를 바꿀 수 있다. 그 밖의
PR은 사람이 쓰는 지시서 `openwiki/INSTRUCTIONS.md`만 바꿀 수 있다. 변경 파일은
`git diff --name-only --no-renames BASE HEAD`로 구한다. `--no-renames`가 없으면
`openwiki/` 밖으로 옮긴 파일이 새 경로만 출력되어 검사를 빠져나간다. 기본값
`HEAD^1`·`HEAD`는 pull_request 이벤트의 merge commit 기준이다. `-z`로 받아 비 ASCII
경로가 따옴표로 감싸여 접두어 비교를 빠져나가지 않게 한다.
"""

import argparse
import subprocess
from collections.abc import Iterable

GENERATED_PREFIX = "openwiki/"
INSTRUCTIONS = "openwiki/INSTRUCTIONS.md"
UPDATE_BRANCH = "openwiki/update"


def changed_paths(base: str, head: str) -> list[str]:
    output = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, head],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [path for path in output.split("\0") if path]


def violations(paths: Iterable[str], *, head_ref: str, same_repo: bool) -> list[str]:
    if head_ref == UPDATE_BRANCH and same_repo:
        return []
    return [
        path
        for path in paths
        # 대소문자 무시 파일 시스템에서 `OpenWiki/`도 같은 폴더로 checkout된다.
        if path.casefold().startswith(GENERATED_PREFIX) and path != INSTRUCTIONS
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="HEAD^1", help="비교 기준 commit")
    parser.add_argument("--head", default="HEAD", help="비교 대상 commit")
    parser.add_argument("--head-ref", required=True, help="PR head 브랜치 이름")
    parser.add_argument(
        "--same-repo",
        required=True,
        choices=("true", "false"),
        help="PR head 저장소가 이 저장소인지 여부",
    )
    args = parser.parse_args(argv)
    found = violations(
        changed_paths(args.base, args.head),
        head_ref=args.head_ref,
        same_repo=args.same_repo == "true",
    )
    if found:
        print(
            f"{GENERATED_PREFIX} 생성물은 같은 저장소의 {UPDATE_BRANCH} 브랜치 PR에서만 "
            f"바꿀 수 있다({INSTRUCTIONS} 제외). 위반 경로:"
        )
        print("\n".join(found))
        return 1
    print("openwiki 생성물 보호 검사 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

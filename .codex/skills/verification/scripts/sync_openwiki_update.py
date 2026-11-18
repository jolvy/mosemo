"""생성 job이 만든 OpenWiki 결과를 검증하고 저장소의 `openwiki/`에 반영한다.

`OpenWiki update` 워크플로의 게시 job이 최신 main checkout에서 실행한다. 생성 job은
모델이 저장소 내용을 읽고 파일을 쓰므로 그 결과를 신뢰하지 않는다. 다음을 모두
통과해야 반영한다.

- 일반 파일만 있다(symlink 금지).
- 확장자가 `.md`·`.json`이다. 실행되거나 git이 읽는 파일이 섞이지 않게 한다.
- `.last-update.json`의 `status`가 `complete`다. 중간에 끝난 갱신을 올리지 않는다.
- 토큰 형태의 문자열(접두어 뒤에 토큰 본문)이 없다. 공개 저장소로 비밀값이 나가지 않게 한다.

사람이 쓰는 지시서 `INSTRUCTIONS.md`는 생성 결과에 있어도 반영하지 않는다. 반영은
지시서를 뺀 기존 `openwiki/` 파일을 지우고 생성 결과로 채우므로 삭제된 페이지도
반영된다.
"""

import argparse
import json
import re
import shutil
from pathlib import Path

INSTRUCTIONS = "INSTRUCTIONS.md"
LAST_UPDATE = ".last-update.json"
ALLOWED_SUFFIXES = (".md", ".json")
# Claude OAuth·API 키와 GitHub 토큰. 위키가 이 검사를 설명하며 접두어만 적는 것은
# 허용하도록 접두어 뒤에 토큰 본문이 이어질 때만 잡는다.
SECRET = re.compile(r"(?:sk-ant-|github_pat_|gh[pousr]_)[A-Za-z0-9_-]{20,}")


def generated_files(generated: Path) -> list[Path]:
    # 폴더를 가리키는 symlink도 걸러지지 않고 검사 대상에 남게 한다.
    return sorted(
        path for path in generated.rglob("*") if path.is_symlink() or not path.is_dir()
    )


def problems(generated: Path) -> list[str]:
    found: list[str] = []
    for path in generated_files(generated):
        relative = path.relative_to(generated).as_posix()
        if path.is_symlink() or not path.is_file():
            found.append(f"{relative}: 일반 파일이 아니다")
        elif not relative.endswith(ALLOWED_SUFFIXES):
            found.append(f"{relative}: 허용하지 않는 확장자다")
        elif SECRET.search(path.read_text(encoding="utf-8", errors="replace")):
            found.append(f"{relative}: 토큰 형태의 문자열이 있다")
    last_update = generated / LAST_UPDATE
    if not last_update.is_file() or last_update.is_symlink():
        found.append(f"{LAST_UPDATE}: 없다")
    elif (
        json.loads(last_update.read_text(encoding="utf-8")).get("status") != "complete"
    ):
        found.append(f"{LAST_UPDATE}: status가 complete가 아니다")
    return found


def sync(generated: Path, target: Path) -> None:
    for path in generated_files(target):
        if path.relative_to(target).as_posix() != INSTRUCTIONS:
            path.unlink()
    for path in generated_files(generated):
        relative = path.relative_to(generated)
        if relative.as_posix() == INSTRUCTIONS:
            continue
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target / relative)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--generated", required=True, type=Path, help="생성 job이 올린 openwiki 결과"
    )
    parser.add_argument(
        "--target", default=Path("openwiki"), type=Path, help="반영할 openwiki 폴더"
    )
    args = parser.parse_args(argv)
    found = problems(args.generated)
    if found:
        print("OpenWiki 생성 결과를 반영하지 않는다:")
        print("\n".join(found))
        return 1
    sync(args.generated, args.target)
    print("OpenWiki 생성 결과 반영")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

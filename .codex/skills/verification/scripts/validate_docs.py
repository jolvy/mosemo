"""신규 문서 계약과 내보낸 Plan Issue 본문의 연결을 검사한다."""

import argparse
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[4]
SKILLS = (
    "ubiquitous-language",
    "requirement-discovery",
    "specification",
    "architecture-decision",
    "engineering-planning",
    "implementation",
    "verification",
)
SKILL_REFERENCES = (
    "engineering-planning/references/domain-modeling.md",
    "engineering-planning/references/persistence-modeling.md",
    "engineering-planning/references/plan-issue.md",
    "engineering-planning/references/propose-tests.md",
    "implementation/references/architecture.md",
    "implementation/references/clean-code.md",
    "implementation/references/code-smells.md",
    "implementation/references/complexity.md",
    "implementation/references/design-patterns.md",
    "implementation/references/object-design.md",
    "implementation/references/solid-principles.md",
    "implementation/references/testing.md",
)
DOCUMENT_SKILLS = {
    "requirements": "requirement-discovery",
    "prd": "specification",
    "adr": "architecture-decision",
}
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
PLACEHOLDER = re.compile(r"<!--|<[^>\n]+>|\b(?:TODO|TBD)\b|(?<!date=)YYYY-MM-DD")


def sections(content: str) -> dict[str, str]:
    # 코드 예시 안의 제목은 실제 섹션으로 취급하지 않는다.
    content = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
    matches = list(re.finditer(r"^## (.+)$", content, re.MULTILINE))
    return {
        match[1]: content[
            match.end() : matches[i + 1].start()
            if i + 1 < len(matches)
            else len(content)
        ].strip()
        for i, match in enumerate(matches)
    }


def required_headings(root: Path, skill: str) -> list[str]:
    relative = (
        "engineering-planning/references/plan-issue.md"
        if skill == "engineering-planning"
        else f"{skill}/SKILL.md"
    )
    content = (root / ".codex" / "skills" / relative).read_text()
    template = re.search(r"```markdown\n(.*?)```", content, re.DOTALL)
    if template is None:
        raise ValueError(f"문서 템플릿 없음: {skill}")
    return list(sections(template[1]))


def check_links(content: str, path: Path, root: Path) -> list[str]:
    errors = []
    for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", content):
        target = target.strip().split(' "', 1)[0].strip("<>")
        parsed = urlsplit(target)
        if parsed.scheme or not parsed.path:
            continue  # 외부 URL과 문서 내 anchor는 오프라인 검사 대상 밖이다.
        resolved = (path.parent / unquote(parsed.path)).resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
            errors.append(f"깨진 로컬 링크: {target}")
    return errors


def check_document(
    content: str, headings: list[str], path: Path, root: Path
) -> list[str]:
    actual = sections(content)
    errors = [
        f"필수 섹션 누락 또는 비어 있음: {h}" for h in headings if not actual.get(h)
    ]
    if PLACEHOLDER.search(content):
        errors.append("미해결 템플릿 자리표시자")
    errors.extend(check_links(content, path, root))
    return errors


def linked_acceptance_ids(content: str, root: Path) -> set[str]:
    identifiers = set()
    for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", content):
        parsed = urlsplit(target.strip().strip("<>"))
        if parsed.scheme:
            continue
        path = (root / unquote(parsed.path)).resolve()
        if path.is_relative_to((root / "docs").resolve()) and path.is_file():
            identifiers.update(
                re.findall(
                    r"\bAC-\d+\b", sections(path.read_text()).get("인수 조건", "")
                )
            )
    return identifiers


def table_rows(content: str) -> list[list[str]]:
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in content.splitlines()
        if line.strip().startswith("|") and not re.fullmatch(r"[| :\-]+", line.strip())
    ]


def check_plan(content: str, root: Path = ROOT) -> list[str]:
    errors = check_document(
        content, required_headings(root, "engineering-planning"), root / "PLAN.md", root
    )
    actual = sections(content)
    approvals = table_rows(actual.get("승인 기록", ""))
    expected = [
        "승인 대상·버전",
        "주요 결정",
        "근거",
        "유효 범위",
        "승인 주체·시점",
        "상태",
    ]
    if not approvals or approvals[0] != expected or len(approvals) < 2:
        errors.append("승인 기록 표의 열 또는 기록 누락")
    else:
        for row in approvals[1:]:
            if (
                len(row) != 6
                or not all(row)
                or row[-1] not in {"유효", "재승인 필요", "미승인", "해당 없음"}
            ):
                errors.append("승인 기록의 값 또는 상태 오류")
    evidence = table_rows(actual.get("테스트 계획과 완료 기준", ""))
    if (
        not evidence
        or evidence[0] != ["인수 조건", "작업", "검증 증거", "상태"]
        or len(evidence) < 2
    ):
        errors.append("인수 조건 추적 표 누락 또는 열 오류")
        return errors
    tasks = set(
        re.findall(r"^- \[[ xX]\] (T\d+)\b", actual.get("작업 계획", ""), re.MULTILINE)
    )
    seen = set()
    for row in evidence[1:]:
        if len(row) != 4 or not all(row):
            errors.append("인수 조건 추적 행의 값 누락")
            continue
        identifier = re.match(r"AC-\d+\b", row[0])
        if identifier is None or identifier[0] in seen:
            errors.append(f"인수 조건 ID 누락 또는 중복: {row[0]}")
        else:
            seen.add(identifier[0])
        linked = set(re.findall(r"\bT\d+\b", row[1]))
        if not linked or linked - tasks:
            errors.append(f"존재하지 않는 작업 연결: {row[1]}")
        if row[3] not in {"통과", "실패", "미검증"}:
            errors.append(f"검증 상태 오류: {row[3]}")
        if row[3] in {"통과", "실패"} and row[2] in {"미실행", "없음", "미검증", "-"}:
            errors.append(f"검증 증거 없음: {row[0]}")
    missing = linked_acceptance_ids(content, root) - seen
    if missing:
        errors.append(f"연결 문서 인수 조건 누락: {', '.join(sorted(missing))}")
    return errors


def validate(root: Path = ROOT) -> list[str]:
    errors = []
    for name in SKILLS:
        path = root / ".codex" / "skills" / name / "SKILL.md"
        if not path.is_file():
            errors.append(f"Skill 없음: {name}")
        elif not re.search(
            r"\A---\nname: " + re.escape(name) + r"\ndescription: .+\n---\n",
            path.read_text(),
        ):
            errors.append(f"Skill frontmatter 오류: {name}")
    for relative in SKILL_REFERENCES:
        path = root / ".codex" / "skills" / relative
        if not path.is_file():
            errors.append(f"Skill 참조 문서 없음: {relative}")
    if errors:
        return errors
    for path in [
        *(root / ".codex" / "skills" / name / "SKILL.md" for name in SKILLS),
        *(root / ".codex" / "skills" / relative for relative in SKILL_REFERENCES),
    ]:
        errors.extend(
            f"{path.relative_to(root)}: {error}"
            for error in check_links(path.read_text(), path, root)
        )
    for folder, skill in DOCUMENT_SKILLS.items():
        headings = required_headings(root, skill)
        for path in sorted((root / "docs" / folder).glob("*.md")):
            relative = path.relative_to(root)
            pattern = rf"{SLUG}\.md"
            if folder == "adr":
                pattern = rf"\d{{4}}-{SLUG}\.md"
            if not re.fullmatch(pattern, path.name):
                errors.append(f"{relative}: 문서 파일명 규칙 위반")
            content = path.read_text()
            errors.extend(
                f"{relative}: {error}"
                for error in check_document(content, headings, path, root)
            )
            if folder in {"requirements", "prd"}:
                identifiers = re.findall(
                    r"\bAC-\d+\b", sections(content).get("인수 조건", "")
                )
                if not identifiers:
                    errors.append(f"{relative}: 인수 조건 ID(AC-번호) 없음")
                if len(identifiers) != len(set(identifiers)):
                    errors.append(f"{relative}: 인수 조건 ID 중복")
            if folder in {"requirements", "adr"} and not re.match(
                r"\A---\nstatus: (draft|proposed|accepted|superseded)\n", content
            ):
                errors.append(f"{relative}: status frontmatter 오류")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan",
        type=Path,
        help="GitHub Plan Issue 본문을 임시 파일로 내보낸 경로",
    )
    args = parser.parse_args()
    errors = validate()
    if args.plan:
        errors.extend(check_plan(args.plan.read_text()))
    print("\n".join(errors) if errors else "하네스 문서 검사 통과")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())

import shutil
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def document_root(tmp_path: Path, docs_validator: ModuleType) -> Path:
    source = docs_validator.ROOT
    # 문서 본문의 경로 검사가 볼 수 있도록 검사 대상 최상위 폴더를 복사한다.
    for prefix in docs_validator.PATH_PREFIXES:
        if (source / prefix).is_dir():
            shutil.copytree(
                source / prefix,
                tmp_path / prefix,
                symlinks=True,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
    pending = list(docs_validator.link_checked_paths(source))
    copied = set()
    while pending:
        path = pending.pop()
        if path in copied or not path.is_relative_to(source):
            continue
        copied.add(path)
        target = tmp_path / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if path.suffix == ".md":
            pending.extend(
                resolved
                for _, resolved in docs_validator.local_links(path.read_text(), path)
                if resolved.exists()
            )
    return tmp_path


@pytest.fixture
def plan_body(docs_validator: ModuleType) -> str:
    headings = docs_validator.required_headings(
        docs_validator.ROOT, "engineering-planning"
    )
    values = dict.fromkeys(headings, "해당 없음")
    values[
        "승인 기록"
    ] = """| 승인 대상·버전 | 주요 결정 | 근거 | 유효 범위 | 승인 주체·시점 | 상태 |
| --- | --- | --- | --- | --- | --- |
| A1 v1 | 문구 보완 | 사용자 명시 승인 요지 | 문구 변경 | 사용자 2026-10-14 | 유효 |"""
    values["작업 계획"] = "- [ ] T1 회귀 검증"
    values["테스트 계획과 완료 기준"] = """| 인수 조건 | 작업 | 검증 증거 | 상태 |
| --- | --- | --- | --- |
| AC-1 현재 계약 보존 | T1 | 환경 준비 전 미실행 | 미검증 |"""
    return "\n\n".join(f"## {heading}\n{values[heading]}" for heading in headings)


def test_plan_allows_honest_not_run_but_rejects_pass_without_evidence(
    docs_validator: ModuleType,
    plan_body: str,
) -> None:
    content = plan_body
    assert docs_validator.check_plan(content) == []
    content = content.replace("환경 준비 전 미실행 | 미검증", "미실행 | 통과")
    assert any("증거 없음" in error for error in docs_validator.check_plan(content))


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("| T1 |", "| T99 |", "작업 연결"),
        ("| 유효 |", "| 알아서 승인 |", "상태 오류"),
        ("AC-1 현재 계약 보존", "조건 설명", "ID 누락"),
        ("## 승인 기록", "## 다른 제목", "필수 섹션"),
        ("현재 계약 보존", "<미확정 조건>", "자리표시자"),
    ],
)
def test_plan_rejects_broken_trace_and_approval_contract(
    docs_validator: ModuleType, plan_body: str, old: str, new: str, expected: str
) -> None:
    assert any(
        expected in error
        for error in docs_validator.check_plan(plan_body.replace(old, new))
    )


def test_code_example_cannot_supply_required_section(
    docs_validator: ModuleType, tmp_path: Path
) -> None:
    assert docs_validator.check_document(
        "```markdown\n## 인수 조건\nAC-1 거짓 섹션\n```",
        ["인수 조건"],
        path=tmp_path / "x.md",
        root=tmp_path,
    )


def test_new_prd_rejects_missing_sections_and_broken_link(
    docs_validator: ModuleType, document_root: Path
) -> None:
    folder = document_root / "docs/prd"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "new-feature.md").write_text("# 새 기능\n[근거](missing.md)")
    errors = docs_validator.validate(document_root)
    assert any("필수 섹션" in error for error in errors)
    assert any("깨진 로컬 링크" in error for error in errors)
    assert any("AC-번호" in error for error in errors)


def test_complete_prd_passes_without_external_schema_files(
    docs_validator: ModuleType,
    document_root: Path,
) -> None:
    folder = document_root / "docs/prd"
    folder.mkdir(parents=True, exist_ok=True)
    content = "\n\n".join(
        f"## {heading}\n"
        + ("AC-1 관찰 가능한 동작" if heading == "인수 조건" else "해당 없음")
        for heading in docs_validator.required_headings(document_root, "specification")
    )
    (folder / "new-feature.md").write_text(content)
    assert docs_validator.validate(document_root) == []


def test_template_comment_is_not_a_filled_section(
    docs_validator: ModuleType, tmp_path: Path
) -> None:
    errors = docs_validator.check_document(
        "## 인수 조건\n<!-- 채울 것 -->",
        ["인수 조건"],
        path=tmp_path / "x.md",
        root=tmp_path,
    )
    assert any("자리표시자" in error for error in errors)


def test_linked_prd_condition_missing_from_plan_is_rejected(
    docs_validator: ModuleType,
    plan_body: str,
    document_root: Path,
) -> None:
    folder = document_root / "docs/prd"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "feature.md").write_text("## 인수 조건\n- AC-1 하나\n- AC-2 둘")
    plan = plan_body.replace(
        "## 요구사항과 기존 결정", "## 요구사항과 기존 결정\n[PRD](docs/prd/feature.md)"
    )
    assert any(
        "AC-2" in error and "누락" in error
        for error in docs_validator.check_plan(plan, document_root)
    )


def test_api_date_format_is_not_an_unresolved_document_date(
    docs_validator: ModuleType, tmp_path: Path
) -> None:
    assert (
        docs_validator.check_document(
            "## 계약\nGET /api/items?date=YYYY-MM-DD",
            ["계약"],
            path=tmp_path / "x.md",
            root=tmp_path,
        )
        == []
    )
    assert any(
        "자리표시자" in error
        for error in docs_validator.check_document(
            "---\ndate: YYYY-MM-DD\n---\n## 계약\n날짜 조회",
            ["계약"],
            path=tmp_path / "x.md",
            root=tmp_path,
        )
    )


@pytest.mark.parametrize("script", ["validate_docs.py", "validate_architecture.py"])
def test_validation_cli_finds_repository_outside_working_directory(
    tmp_path: Path, docs_validator: ModuleType, script: str
) -> None:
    import subprocess
    import sys

    path = docs_validator.ROOT / ".codex/skills/verification/scripts" / script
    result = subprocess.run(
        [sys.executable, str(path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_plan_template_is_loaded_from_reference(
    docs_validator: ModuleType, document_root: Path, plan_body: str
) -> None:
    template = (
        document_root / ".codex/skills/engineering-planning/references/plan-issue.md"
    )
    template.write_text(
        template.read_text().replace("## 위험·미결정 사항", "## 추가 검토 사항")
    )
    assert any(
        "추가 검토 사항" in error
        for error in docs_validator.check_plan(plan_body, document_root)
    )


@pytest.mark.parametrize(
    "relative",
    [
        "engineering-planning/references/plan-issue.md",
        "engineering-planning/references/plan-review.md",
        "engineering-planning/references/propose-tests.md",
    ],
)
def test_missing_skill_reference_is_reported_without_crashing(
    docs_validator: ModuleType, document_root: Path, relative: str
) -> None:
    (document_root / ".codex/skills" / relative).unlink()
    assert any(
        "Skill 참조 문서 없음" in error and relative in error
        for error in docs_validator.validate(document_root)
    )


@pytest.mark.parametrize(
    "relative",
    [
        "engineering-planning/SKILL.md",
        "engineering-planning/references/plan-review.md",
    ],
)
def test_broken_skill_reference_is_rejected(
    docs_validator: ModuleType, document_root: Path, relative: str
) -> None:
    path = document_root / ".codex/skills" / relative
    path.write_text(path.read_text() + "\n[추가 절차](references/missing.md)\n")
    assert any(
        "깨진 로컬 링크" in error for error in docs_validator.validate(document_root)
    )


@pytest.mark.parametrize(
    "relative", ["docs/code-style-rubric.md", "src/mosemo/AGENTS.md"]
)
def test_broken_link_in_repository_document_is_rejected(
    docs_validator: ModuleType, document_root: Path, relative: str
) -> None:
    path = document_root / relative
    path.write_text(path.read_text() + "\n[없는 문서](missing.md)\n")
    assert any(
        relative in error and "깨진 로컬 링크" in error
        for error in docs_validator.validate(document_root)
    )


@pytest.fixture
def path_root(tmp_path: Path) -> Path:
    for relative in (
        "src/mosemo/__init__.py",
        "src/mosemo/shared/errors.py",
        "src/mosemo/labels/domain/label.py",
        "tests/a.py",
    ):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("")
    return tmp_path


def test_missing_repository_path_is_rejected(
    docs_validator: ModuleType, path_root: Path
) -> None:
    assert docs_validator.check_paths("본문 `src/missing.py`", path_root) == [
        "없는 경로: src/missing.py"
    ]


def test_line_and_test_name_suffix_is_removed_before_check(
    docs_validator: ModuleType, path_root: Path
) -> None:
    content = "`src/mosemo/__init__.py:12`, `tests/a.py::test_x`"
    assert docs_validator.check_paths(content, path_root) == []


def test_placeholder_is_skipped_and_glob_needs_one_match(
    docs_validator: ModuleType, path_root: Path
) -> None:
    content = (
        "`docs/prd/<기능-슬러그>.md`, `tests/unit/{패키지}/`, `src/mosemo/*/domain/`"
    )
    assert docs_validator.check_paths(content, path_root) == []
    assert docs_validator.check_paths("`src/mosemo/*/missing/`", path_root) == [
        "없는 경로: src/mosemo/*/missing/"
    ]


def test_top_level_module_name_is_not_treated_as_attribute(
    docs_validator: ModuleType, path_root: Path
) -> None:
    assert docs_validator.check_paths("`mosemo.billing`", path_root) == [
        "없는 경로: mosemo.billing"
    ]


def test_module_path_may_end_with_one_attribute(
    docs_validator: ModuleType, path_root: Path
) -> None:
    content = (
        "`mosemo.missing.mod`, `mosemo.shared`, `mosemo.shared.errors.DomainError`,"
        " `mosemo.labels.domain.label`"
    )
    assert docs_validator.check_paths(content, path_root) == [
        "없는 경로: mosemo.missing.mod"
    ]


def test_adr_fence_and_allowlist_are_not_path_checked(
    docs_validator: ModuleType, document_root: Path
) -> None:
    adr = min((document_root / "docs/adr").glob("*.md"))
    adr.write_text(adr.read_text() + "\n`src/missing.py`\n")
    path = document_root / "docs/code-style-rubric.md"
    path.write_text(
        path.read_text()
        + "\n```text\n`src/missing.py`\n```\n`docs/plans/`, `tests/e2e/test_x.py`\n"
    )
    assert docs_validator.validate(document_root) == []
    path.write_text(path.read_text() + "\n`src/missing.py`\n")
    assert docs_validator.validate(document_root) == [
        "docs/code-style-rubric.md: 없는 경로: src/missing.py"
    ]


def test_prd_body_is_path_checked(
    docs_validator: ModuleType, document_root: Path
) -> None:
    path = min((document_root / "docs/prd").glob("*.md"))
    path.write_text(path.read_text() + "\n`mosemo.missing.mod`\n")
    assert docs_validator.validate(document_root) == [
        f"{path.relative_to(document_root)}: 없는 경로: mosemo.missing.mod"
    ]


def test_plan_body_is_not_path_checked(
    docs_validator: ModuleType, plan_body: str
) -> None:
    plan = plan_body.replace("T1 회귀 검증", "T1 `src/mosemo/new_module.py` 추가")
    assert docs_validator.check_plan(plan) == []

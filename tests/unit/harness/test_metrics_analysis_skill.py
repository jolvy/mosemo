"""지표 로그 분석 Skill 문서의 구조·필수 문구와 집계 명령이 `aggregate.py`와 맞는다.

저장소 파일 읽기와 순수 함수 호출만 한다. git·subprocess를 부르지 않는다.
"""

import re
import shlex
from pathlib import Path
from types import ModuleType

import pytest

SKILL = ".codex/skills/agent-metrics-analysis/SKILL.md"
AGGREGATE = ".codex/reports/aggregate.py"
SECTIONS = [
    "시작과 읽을 자료",
    "절차",
    "판단 기준",
    "보고서 형식",
    "금지",
    "종료 조건",
]
FENCE = re.compile(
    r"^[ \t]*```([^\n`]*)\n(.*?)^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL
)
FLAGS = {
    "--log",
    "--start",
    "--end",
    "--home",
    "--main-checkout",
    "--worktrees-root",
}
EXAMPLE_VALUES = {"--start": "2026-11-01T00:00:00Z", "--end": "2026-11-01T00:00:00Z"}


@pytest.fixture(scope="module")
def skill(repository: Path) -> str:
    return (repository / SKILL).read_text()


def fences(content: str, language: str) -> list[str]:
    return [
        m.group(2) for m in FENCE.finditer(content) if m.group(1).strip() == language
    ]


def outside_fences(content: str) -> str:
    return FENCE.sub("", content)


def headings(content: str) -> list[str]:
    return re.findall(r"^## (.+)$", outside_fences(content), re.MULTILINE)


def section(content: str, name: str) -> str:
    """펜스 밖 `## ` 제목으로 나눈 절 본문(펜스 블록 포함)."""
    matches = [
        match
        for match in re.finditer(r"^## (.+)$", content, re.MULTILINE)
        if match.group(1) in SECTIONS
    ]
    for index, match in enumerate(matches):
        if match.group(1) == name:
            end = matches[index + 1].start() if index + 1 < len(matches) else None
            return content[match.end() : end]
    raise AssertionError(name)


# TC-14 문서 구조·필수 문구


def test_frontmatter_matches_docs_validator(skill: str) -> None:
    pattern = r"\A---\nname: agent-metrics-analysis\ndescription: .+\n---\n"

    assert re.search(pattern, skill)


def test_headings_outside_fences_are_the_six_sections(skill: str) -> None:
    assert headings(skill) == SECTIONS


@pytest.mark.parametrize(
    ("name", "phrases"),
    [
        (
            "절차",
            [
                "최근 7일",
                "session_files",
                "panel_unreadable_sessions",
                "~/mosemo-reports/",
            ],
        ),
        (
            "판단 기준",
            [
                "사용자 정정",
                "권한 거부",
                "같은 실패의 반복",
                "리뷰 지적 종류",
                "비교 불가(편집 줄 없음)",
            ],
        ),
        ("금지", ["`gh`", "`git push`", "session_files"]),
    ],
)
def test_section_contains_required_phrases(
    skill: str, name: str, phrases: list[str]
) -> None:
    body = section(skill, name)

    for phrase in phrases:
        assert phrase in body


def test_report_template_has_sections_in_order(skill: str) -> None:
    body = section(skill, "보고서 형식")
    [template] = fences(body, "markdown")
    titles = ["## 1. 결과", "## 2. 최근 며칠의 양상", "## 3. 개선해야 할 점"]
    lines = template.splitlines()

    positions = [lines.index(title) for title in [*titles, "## 실행 정보"]]

    assert positions == sorted(positions)
    assert "판단값" in body


def test_document_does_not_use_forbidden_word(skill: str) -> None:
    assert "주간" not in skill


# TC-15 집계 명령 일치


def aggregate_line(skill: str) -> str:
    lines = [
        line
        for block in fences(skill, "sh")
        for line in block.splitlines()
        if AGGREGATE in line
    ]
    assert len(lines) == 1
    return lines[0]


def command_arguments(skill: str) -> list[str]:
    tokens = shlex.split(aggregate_line(skill))
    return tokens[tokens.index(AGGREGATE) + 1 :]


def test_aggregate_command_uses_exactly_the_cli_flags(skill: str) -> None:
    flags = {token for token in command_arguments(skill) if token.startswith("--")}

    assert flags == FLAGS


def test_aggregate_command_is_accepted_by_parse_args(
    skill: str, report_aggregate: ModuleType
) -> None:
    arguments = command_arguments(skill)
    example = list(arguments)
    for index, token in enumerate(arguments[:-1]):
        if token in FLAGS:
            example[index + 1] = EXAMPLE_VALUES.get(token, "/tmp/x")

    args = report_aggregate.parse_args(example)

    assert args.log == Path("/tmp/x")
    assert args.worktrees_root == Path("/tmp/x")

"""Claude Code agent 래퍼(`.claude/agents/<이름>.md`)가 `.codex/agents/<이름>.toml`과
1:1로 대응하고, effort가 toml의 model_reasoning_effort와 같으며, toml을 가리키기만 하는지
검사한다."""

import tomllib
from pathlib import Path

import pytest

AGENTS = ".codex/agents"
WRAPPERS = ".claude/agents"


def expected_body(name: str) -> str:
    return (
        f"{AGENTS}/{name}.toml의 developer_instructions를 읽고 그대로 따른다. "
        "읽기 전용으로 검토한다."
    )


def split_wrapper(text: str) -> tuple[dict[str, str], str] | None:
    """첫 두 `---` 줄 사이의 `key: value`와 그 뒤 본문을 나눈다."""
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return None
    end = lines.index("---", 1)
    fields = {
        key.strip(): value.strip()
        for key, separator, value in (line.partition(":") for line in lines[1:end])
        if separator
    }
    return fields, "\n".join(lines[end + 1 :]).strip()


def wrapper_errors_for(path: Path, agent: dict[str, str]) -> list[str]:
    split = split_wrapper(path.read_text(encoding="utf-8"))
    if split is None:
        return [f"{path}: frontmatter가 `---` 두 줄로 감싸져 있지 않다"]
    fields, body = split
    name = agent["name"]
    errors = []
    if fields.get("name") != name:
        errors.append(f"{path}: name이 toml의 name({name})과 다르다")
    if fields.get("description") != agent["description"]:
        errors.append(f"{path}: description이 toml의 description과 다르다")
    if fields.get("effort") != agent.get("model_reasoning_effort"):
        errors.append(
            f"{path}: effort가 toml의 model_reasoning_effort"
            f"({agent.get('model_reasoning_effort')})와 다르다"
        )
    if body != expected_body(name):
        errors.append(f"{path}: 본문이 toml을 가리키는 두 문장과 다르다")
    return errors


def wrapper_errors(root: Path) -> list[str]:
    agents = {}
    for path in sorted((root / AGENTS).glob("*.toml")):
        agent = tomllib.loads(path.read_text(encoding="utf-8"))
        agents[agent["name"]] = agent
    wrappers = {path.stem: path for path in (root / WRAPPERS).glob("*.md")}
    errors = [
        f"{WRAPPERS}/{name}.md: {AGENTS}의 {name}에 대응하는 래퍼가 없다"
        for name in sorted(agents.keys() - wrappers.keys())
    ]
    errors.extend(
        f"{WRAPPERS}/{name}.md: 대응하는 {AGENTS}/*.toml이 없다"
        for name in sorted(wrappers.keys() - agents.keys())
    )
    for name in sorted(agents.keys() & wrappers.keys()):
        errors.extend(wrapper_errors_for(wrappers[name], agents[name]))
    return errors


def write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def write_agent(
    root: Path, name: str = "reviewer", effort: str | None = "medium"
) -> None:
    effort_line = "" if effort is None else f'model_reasoning_effort = "{effort}"\n'
    write(
        root,
        f"{AGENTS}/{name}.toml",
        f'name = "{name}"\ndescription = "Read-only reviewer."\n'
        f'{effort_line}developer_instructions = "Review."\n',
    )


def wrapper_source(
    name: str = "reviewer",
    body: str | None = None,
    description: str = "Read-only reviewer.",
    effort: str | None = "medium",
) -> str:
    effort_line = "" if effort is None else f"effort: {effort}\n"
    return (
        f"---\nname: {name}\ndescription: {description}\n{effort_line}"
        "tools: Read, Grep, Glob, Bash\n---\n\n"
        f"{expected_body(name) if body is None else body}\n"
    )


def test_repository_agent_wrappers_match_tomls(repository: Path) -> None:
    assert wrapper_errors(repository) == []


def test_matching_wrapper_passes(tmp_path: Path) -> None:
    write_agent(tmp_path)
    write(tmp_path, f"{WRAPPERS}/reviewer.md", wrapper_source())

    assert wrapper_errors(tmp_path) == []


def test_missing_wrapper_is_rejected(tmp_path: Path) -> None:
    write_agent(tmp_path)

    assert len(wrapper_errors(tmp_path)) >= 1


def test_wrapper_without_toml_is_rejected(tmp_path: Path) -> None:
    write_agent(tmp_path)
    write(tmp_path, f"{WRAPPERS}/reviewer.md", wrapper_source())
    write(tmp_path, f"{WRAPPERS}/orphan.md", wrapper_source(name="orphan"))

    assert len(wrapper_errors(tmp_path)) >= 1


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            wrapper_source().replace("name: reviewer", "name: other"), id="name"
        ),
        pytest.param(wrapper_source(description="Copied loosely."), id="description"),
        pytest.param(wrapper_source(effort="high"), id="effort"),
        pytest.param(wrapper_source(effort=None), id="missing-effort"),
        pytest.param(wrapper_source(body="Review."), id="body"),
        pytest.param(
            wrapper_source(body=f"{expected_body('reviewer')}\n\nReview."),
            id="copied-body",
        ),
        pytest.param(expected_body("reviewer"), id="no-frontmatter"),
    ],
)
def test_invalid_wrapper_is_rejected(tmp_path: Path, source: str) -> None:
    write_agent(tmp_path)
    write(tmp_path, f"{WRAPPERS}/reviewer.md", source)

    assert len(wrapper_errors(tmp_path)) >= 1


def test_effort_without_toml_key_is_rejected(tmp_path: Path) -> None:
    write_agent(tmp_path, effort=None)
    write(tmp_path, f"{WRAPPERS}/reviewer.md", wrapper_source())

    assert len(wrapper_errors(tmp_path)) >= 1

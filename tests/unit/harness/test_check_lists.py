"""하네스의 검사 목록이 Skill 폴더와 루트 `AGENTS.md` 「빌드와 검증」보다 약해지지 않는다."""

import re
from pathlib import Path
from types import ModuleType


def test_docs_validator_checks_every_skill(
    docs_validator: ModuleType, repository: Path
) -> None:
    skills = {
        path.parent.name for path in (repository / ".codex/skills").glob("*/SKILL.md")
    }

    assert set(docs_validator.SKILLS) == skills


def test_validate_sh_runs_every_agents_md_verification(repository: Path) -> None:
    agents = (repository / "AGENTS.md").read_text()
    section = agents.split("## 빌드와 검증", 1)[1]
    block = re.search(r"```sh\n(.*?)```", section, re.DOTALL)
    assert block is not None
    required = {line.strip() for line in block.group(1).splitlines() if line.strip()}
    script = repository / ".codex/skills/verification/scripts/validate.sh"
    lines = {line.strip() for line in script.read_text().splitlines()}

    assert required - lines == set()


def test_stop_gate_style_checks_include_lint_imports(stop_gate: ModuleType) -> None:
    assert "lint-imports" in {name for name, _ in stop_gate.STYLE_CHECKS}

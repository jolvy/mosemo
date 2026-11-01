"""검증 Skill과 에이전트 hook의 스크립트를 배포 패키지나 sys.path 변경 없이 불러온다."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPTS = REPOSITORY / ".codex/skills/verification/scripts"
HOOKS = REPOSITORY / ".codex/hooks"


def load_validator(name: str) -> ModuleType:
    return load_script(SCRIPTS / f"{name}.py")


def load_script(path: Path) -> ModuleType:
    spec = spec_from_file_location(path.stem, path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def docs_validator() -> ModuleType:
    return load_validator("validate_docs")


@pytest.fixture(scope="session")
def architecture_validator() -> ModuleType:
    return load_validator("validate_architecture")


@pytest.fixture(scope="session")
def conventions_validator() -> ModuleType:
    return load_validator("validate_conventions")


@pytest.fixture
def post_edit_hook() -> ModuleType:
    return load_script(HOOKS / "post_edit_feedback.py")


@pytest.fixture
def stop_hook() -> ModuleType:
    return load_script(HOOKS / "stop_gate.py")

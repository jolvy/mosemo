"""검증 Skill의 스크립트를 배포 패키지나 sys.path 변경 없이 불러온다."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / ".codex/skills/verification/scripts"


def load_validator(name: str) -> ModuleType:
    spec = spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def docs_validator() -> ModuleType:
    return load_validator("validate_docs")


@pytest.fixture(scope="session")
def architecture_validator() -> ModuleType:
    return load_validator("validate_architecture")

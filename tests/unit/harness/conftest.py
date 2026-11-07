"""검증 Skill과 hook의 스크립트를 배포 패키지나 sys.path 변경 없이 불러온다."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPTS = REPOSITORY / ".codex/skills/verification/scripts"


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


@pytest.fixture(scope="session")
def stop_metrics() -> ModuleType:
    return load_script(REPOSITORY / ".codex/hooks/stop_metrics.py")


@pytest.fixture(scope="session")
def report_aggregate() -> ModuleType:
    return load_script(REPOSITORY / ".codex/reports/aggregate.py")


@pytest.fixture
def report_runner(
    monkeypatch: pytest.MonkeyPatch, report_aggregate: ModuleType
) -> ModuleType:
    # run_daily.py는 최상위에서 `import aggregate`를 하므로 sys.path 대신 sys.modules에 넣는다.
    monkeypatch.setitem(sys.modules, "aggregate", report_aggregate)
    return load_script(REPOSITORY / ".codex/reports/run_daily.py")


@pytest.fixture(scope="session")
def report_launchd() -> ModuleType:
    return load_script(REPOSITORY / ".codex/reports/install_launchd.py")


@pytest.fixture
def stop_gate(monkeypatch: pytest.MonkeyPatch, stop_metrics: ModuleType) -> ModuleType:
    # stop_gate.py는 최상위에서 `import stop_metrics`를 하므로 sys.path 대신 sys.modules에 넣는다.
    monkeypatch.setitem(sys.modules, "stop_metrics", stop_metrics)
    return load_script(REPOSITORY / ".codex/hooks/stop_gate.py")


@pytest.fixture(scope="session")
def repository() -> Path:
    return REPOSITORY

"""검증 Skill과 hook의 스크립트를 배포 패키지 없이 불러온다. sys.path 변경은 되돌린다."""

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
SCRIPTS = REPOSITORY / ".codex/skills/verification/scripts"


@pytest.fixture(autouse=True)
def isolate_hook_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """hook이 넣는 MOSEMO_AGENT·MOSEMO_HOOK_LAYER가 테스트로 새지 않게 지운다.

    Codex hook(`stop_gate.py`)은 이 값을 가진 환경을 pytest에 그대로 넘기므로
    지우지 않으면 hook 안의 실행과 직접 실행의 결과가 달라진다. 값이 필요한
    테스트는 `monkeypatch.setenv`로 직접 넣는다.
    """
    monkeypatch.delenv("MOSEMO_AGENT", raising=False)
    monkeypatch.delenv("MOSEMO_HOOK_LAYER", raising=False)


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
def full_validator(
    monkeypatch: pytest.MonkeyPatch, stop_metrics: ModuleType
) -> ModuleType:
    # validate.py는 `.codex/hooks`를 sys.path에 넣고 `import stop_metrics`를 하므로
    # sys.modules에 넣고 sys.path는 테스트 뒤 되돌린다.
    monkeypatch.setitem(sys.modules, "stop_metrics", stop_metrics)
    monkeypatch.setattr(sys, "path", list(sys.path))
    return load_validator("validate")


@pytest.fixture(scope="session")
def stop_metrics() -> ModuleType:
    return load_script(REPOSITORY / ".codex/hooks/stop_metrics.py")


@pytest.fixture(scope="session")
def report_aggregate() -> ModuleType:
    return load_script(REPOSITORY / ".codex/reports/aggregate.py")


@pytest.fixture
def review_metrics(
    monkeypatch: pytest.MonkeyPatch, stop_metrics: ModuleType
) -> ModuleType:
    monkeypatch.setitem(sys.modules, "stop_metrics", stop_metrics)
    return load_script(REPOSITORY / ".codex/hooks/review_metrics.py")


@pytest.fixture(scope="session")
def install_user_hooks() -> ModuleType:
    return load_script(REPOSITORY / ".codex/hooks/install_user_hooks.py")


@pytest.fixture
def stop_gate(
    monkeypatch: pytest.MonkeyPatch,
    stop_metrics: ModuleType,
    review_metrics: ModuleType,
) -> ModuleType:
    # stop_gate.py는 최상위에서 `import stop_metrics`·`import review_metrics`를 하므로
    # sys.path 대신 sys.modules에 넣는다.
    monkeypatch.setitem(sys.modules, "stop_metrics", stop_metrics)
    monkeypatch.setitem(sys.modules, "review_metrics", review_metrics)
    return load_script(REPOSITORY / ".codex/hooks/stop_gate.py")


@pytest.fixture
def post_edit_feedback(
    monkeypatch: pytest.MonkeyPatch,
    stop_metrics: ModuleType,
) -> ModuleType:
    monkeypatch.setitem(sys.modules, "stop_metrics", stop_metrics)
    return load_script(REPOSITORY / ".codex/hooks/post_edit_feedback.py")


@pytest.fixture
def skill_evals(
    monkeypatch: pytest.MonkeyPatch, stop_metrics: ModuleType
) -> ModuleType:
    # run.py는 `.codex/hooks`를 sys.path에 넣고 `import stop_metrics`를 하므로
    # sys.modules에 넣고 sys.path는 테스트 뒤 되돌린다.
    monkeypatch.setitem(sys.modules, "stop_metrics", stop_metrics)
    monkeypatch.setattr(sys, "path", list(sys.path))
    return load_script(REPOSITORY / ".codex/evals/run.py")


@pytest.fixture(scope="session")
def repository() -> Path:
    return REPOSITORY

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
SEED_LOAD = SCRIPTS / "seed_load.py"


@pytest.fixture(scope="session")
def seed_load() -> ModuleType:
    spec = spec_from_file_location("seed_load", SEED_LOAD)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def label_eval_score() -> ModuleType:
    spec = spec_from_file_location("label_eval_score", SCRIPTS / "label_eval_score.py")
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def label_eval_cases() -> ModuleType:
    spec = spec_from_file_location("label_eval_cases", SCRIPTS / "label_eval_cases.py")
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def label_eval_run() -> ModuleType:
    spec = spec_from_file_location("label_eval_run", SCRIPTS / "label_eval_run.py")
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

SEED_LOAD = Path(__file__).resolve().parents[3] / "scripts/seed_load.py"


@pytest.fixture(scope="session")
def seed_load() -> ModuleType:
    spec = spec_from_file_location("seed_load", SEED_LOAD)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

from types import ModuleType

import pytest


@pytest.mark.parametrize(
    "source",
    [
        "import fastapi",
        "from starlette.requests import Request",
        "from mosemo.accounts import router",
        "from . import router",
        "from .router import register",
        "def hidden():\n    from fastapi import Request",
    ],
)
def test_models_reject_http_imports_including_relative_and_nested(
    architecture_validator: ModuleType, source: str
) -> None:
    assert architecture_validator.check_source(source, "mosemo.accounts.models")


@pytest.mark.parametrize("module", ["mosemo.exceptions", "mosemo.responses"])
def test_runtime_error_modules_cannot_import_openapi(
    architecture_validator: ModuleType, module: str
) -> None:
    assert architecture_validator.check_source("from mosemo import openapi", module)


def test_repository_keeps_orm_and_schema_dependencies(
    architecture_validator: ModuleType,
) -> None:
    assert not architecture_validator.check_source(
        "from sqlalchemy import select\nfrom .schemas import Request",
        "mosemo.accounts.repository",
    )


def test_real_repository_satisfies_declared_boundaries(
    architecture_validator: ModuleType,
) -> None:
    assert architecture_validator.validate() == []

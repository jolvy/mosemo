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


@pytest.mark.parametrize(
    "module", ["mosemo.exceptions", "mosemo.shared.presentation.responses"]
)
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


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("mosemo.x.receipt_repository", "from .service import S"),
        ("mosemo.x.receipt_repository", "from .router import r"),
        ("mosemo.x.schemas", "from .receipt_repository import R"),
        ("mosemo.x.repository", "import mosemo.x.service"),
        ("mosemo.x.repository", "from mosemo.x.service import S"),
        ("mosemo.x.models", "from .repository import R"),
        ("mosemo.x.service", "from mosemo.x.router import r"),
        ("mosemo.x.service", "from mosemo.api.dependencies import d"),
        ("mosemo.x.mappers", "from mosemo.y.service import S"),
        ("mosemo.x.schemas", "from mosemo.x.repository import R"),
        ("mosemo.x.service", "from mosemo.x import router"),
    ],
)
def test_role_files_reject_outward_imports(
    architecture_validator: ModuleType, module: str, source: str
) -> None:
    errors = architecture_validator.check_source(source, module)

    assert any("CS-P32" in error for error in errors)


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("mosemo.x.router", "from mosemo.x.service import S"),
        ("mosemo.x.service", "from mosemo.x.mappers import M"),
        ("mosemo.x.schemas", "from mosemo.x.models import E"),
        ("mosemo.x.service", "from mosemo.y.service import f"),
        ("mosemo.x.executor", "from mosemo.x.router import r"),
        ("mosemo.x.repository", "from .schemas import Request"),
        ("mosemo.shared.presentation.schemas", "from mosemo.x.models import E"),
        ("mosemo.x.repository", "from fastapi import dependencies"),
    ],
)
def test_role_files_allow_inward_same_role_and_unlisted_imports(
    architecture_validator: ModuleType, module: str, source: str
) -> None:
    errors = architecture_validator.check_source(source, module)

    assert not any("CS-P32" in error for error in errors)


@pytest.mark.parametrize(
    "source",
    [
        "from mosemo.accounts import router",
        "from . import router",
        "from .router import register",
    ],
)
def test_role_direction_leaves_http_rule_violations_to_http_rule(
    architecture_validator: ModuleType, source: str
) -> None:
    errors = architecture_validator.check_source(source, "mosemo.accounts.models")

    assert errors
    assert not any("CS-P32" in error for error in errors)


def test_role_direction_error_names_rule_and_direction(
    architecture_validator: ModuleType,
) -> None:
    expected = (
        "1: mosemo.x.service: CS-P32: `repository`가 `service`를 import한다. "
        "의존은 router → dependencies → service → mappers → repository·schemas → models"
        " 방향으로만 둔다(방향만 정하며 개별 금지는 CS-P28·CS-P29)."
        " 필요한 값은 인자로 받거나 안쪽 역할로 옮긴다"
    )

    errors = architecture_validator.check_source(
        "from .service import S", "mosemo.x.repository"
    )

    assert errors == [expected]

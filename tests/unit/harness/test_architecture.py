import ast
from types import ModuleType

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "mosemo.shared.errors",
        "mosemo.shared.presentation.responses",
        "mosemo.api.exception_handlers",
    ],
)
@pytest.mark.parametrize(
    "source",
    [
        "from mosemo.shared import openapi",
        "from mosemo.shared.error_docs import ERROR_DOCS",
        "from mosemo.api.openapi import public_openapi",
        "def hidden():\n    import mosemo.api.openapi",
    ],
)
def test_runtime_error_modules_cannot_import_openapi(
    architecture_validator: ModuleType, module: str, source: str
) -> None:
    assert architecture_validator.check_source(source, module)


def test_other_modules_may_import_openapi(
    architecture_validator: ModuleType,
) -> None:
    assert not architecture_validator.check_source(
        "from mosemo.shared.openapi import api_error_responses", "mosemo.api.routes"
    )


def test_real_repository_satisfies_declared_boundaries(
    architecture_validator: ModuleType,
) -> None:
    assert architecture_validator.validate() == []


def test_alembic_env_maps_every_context_only_through_its_bootstrap(
    architecture_validator: ModuleType,
) -> None:
    root = architecture_validator.ROOT
    contexts = {path.name for path in architecture_validator.context_dirs(root)}
    tree = ast.parse((root / "migrations" / "env.py").read_text())
    context_imports = {
        (node.module, alias.name): alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.split(".")[:2][-1] in contexts
        for alias in node.names
    }
    mapped = {
        node.func.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "map_tables"
        and isinstance(node.func.value, ast.Name)
    }

    assert set(context_imports) == {
        (f"mosemo.{context}", "bootstrap") for context in contexts
    }
    assert mapped == set(context_imports.values())

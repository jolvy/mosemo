"""Every process assembly root registers the analytics handlers.

If the consumer process skipped them, a late observation would silently lose
its recompute mark.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ASSEMBLY_ROOTS = (
    "src/mosemo/entrypoints/api/contexts.py",
    "src/mosemo/entrypoints/consumer/activity_batches.py",
    "src/mosemo/entrypoints/batch/registry.py",
)
REGISTRATION = "analytics_bootstrap.register_handlers("


def registers_analytics_handlers(source: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register_handlers"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "analytics_bootstrap"
        for node in ast.walk(ast.parse(source))
    )


def roots_without_registration(sources: dict[str, str]) -> list[str]:
    return [
        path
        for path, source in sources.items()
        if not registers_analytics_handlers(source)
    ]


def repository_sources() -> dict[str, str]:
    return {path: (ROOT / path).read_text() for path in ASSEMBLY_ROOTS}


def test_every_assembly_root_registers_analytics_handlers() -> None:
    assert roots_without_registration(repository_sources()) == []


@pytest.mark.parametrize("removed", ASSEMBLY_ROOTS)
def test_root_without_the_registration_is_reported(removed: str) -> None:
    sources = repository_sources()
    lines = sources[removed].splitlines(keepends=True)
    # Drop every statement line of the call; the call spans several lines.
    calls = [
        node
        for node in ast.walk(ast.parse(sources[removed]))
        if isinstance(node, ast.Expr) and REGISTRATION in ast.unparse(node)
    ]
    dropped = {
        index
        for node in calls
        for index in range(node.lineno - 1, node.end_lineno or node.lineno)
    }
    assert dropped
    sources[removed] = "".join(
        line for index, line in enumerate(lines) if index not in dropped
    )

    assert roots_without_registration(sources) == [removed]


def calls_registration_function(source: str) -> bool:
    """The root runs its registration, not only defines the function."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register_analytics_handlers"
        for node in ast.walk(ast.parse(source))
    )


@pytest.mark.parametrize("root", ASSEMBLY_ROOTS)
def test_every_assembly_root_calls_its_registration_function(root: str) -> None:
    assert calls_registration_function(repository_sources()[root])

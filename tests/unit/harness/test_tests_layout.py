"""tests/ 구조 규칙: `__init__.py` 위치(H3)와 테스트 코드 사이 import 방향(H4)."""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

TEST_MODULES = ("tests.unit", "tests.integration", "tests.e2e")


def python_files(root: Path) -> list[Path]:
    tests = root / "tests"
    return sorted(
        path
        for path in tests.rglob("*.py")
        if not any(
            part == "__pycache__" or part.startswith(".")
            for part in path.relative_to(tests).parts
        )
    )


def init_file_errors(root: Path) -> list[str]:
    errors = []
    for path in python_files(root):
        parts = path.relative_to(root).parts
        if path.name != "__init__.py" or parts == ("tests", "__init__.py"):
            continue
        if parts[:2] != ("tests", "support"):
            errors.append(
                f"{path.relative_to(root)}: __init__.py는 tests/__init__.py와 "
                "tests/support/ 아래에만 둔다"
            )
    return errors


def imported_names(path: Path, root: Path) -> Iterator[str]:
    parts = path.relative_to(root).with_suffix("").parts
    package = parts[:-1]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else ()
            module = ".".join([*base, *([node.module] if node.module else [])])
            yield from (f"{module}.{alias.name}" for alias in node.names)


def within(name: str, *modules: str) -> bool:
    return any(name == module or name.startswith(f"{module}.") for module in modules)


def import_errors(root: Path) -> list[str]:
    errors = []
    for path in python_files(root):
        in_support = path.relative_to(root).parts[:2] == ("tests", "support")
        for name in imported_names(path, root):
            if in_support and within(name, *TEST_MODULES):
                errors.append(
                    f"{path.relative_to(root)}: tests/support는 테스트 모듈 {name}을 "
                    "import하지 않는다"
                )
            elif (
                not in_support
                and within(name, "tests")
                and not within(name, "tests.support")
            ):
                errors.append(
                    f"{path.relative_to(root)}: tests 아래에서는 tests.support만 "
                    f"import한다({name})"
                )
    return errors


def write(root: Path, relative: str, source: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_repository_keeps_init_files_in_tests_root_and_support(
    repository: Path,
) -> None:
    assert init_file_errors(repository) == []


def test_repository_test_modules_import_only_support(repository: Path) -> None:
    assert import_errors(repository) == []


@pytest.mark.parametrize(
    "relative",
    [
        "tests/unit/__init__.py",
        "tests/unit/support/__init__.py",
        "tests/integration/labels/__init__.py",
        "tests/supporting/__init__.py",
    ],
)
def test_init_file_outside_tests_root_and_support_is_rejected(
    tmp_path: Path, relative: str
) -> None:
    write(tmp_path, "tests/__init__.py")
    write(tmp_path, relative)

    assert init_file_errors(tmp_path) == [
        f"{relative}: __init__.py는 tests/__init__.py와 tests/support/ 아래에만 둔다"
    ]


def test_init_files_in_tests_root_support_and_ignored_entries_pass(
    tmp_path: Path,
) -> None:
    for relative in [
        "tests/__init__.py",
        "tests/support/__init__.py",
        "tests/support/processes/__init__.py",
        "tests/unit/__pycache__/__init__.py",
        "tests/.venv/__init__.py",
    ]:
        write(tmp_path, relative)

    assert init_file_errors(tmp_path) == []


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("tests/unit/shared/test_a.py", "from tests.integration.test_b import helper"),
        ("tests/unit/shared/test_a.py", "import tests.unit.test_b"),
        ("tests/unit/shared/test_a.py", "from tests import conftest"),
        ("tests/conftest.py", "from tests.unit.harness.conftest import REPOSITORY"),
        ("tests/support/clock.py", "from tests.unit.test_b import helper"),
        ("tests/support/clock.py", "def f():\n    import tests.e2e.test_c"),
        ("tests/support/clock.py", "from ..integration import test_b"),
    ],
)
def test_import_outside_support_direction_is_rejected(
    tmp_path: Path, relative: str, source: str
) -> None:
    write(tmp_path, relative, source)

    assert len(import_errors(tmp_path)) == 1


def test_imports_of_support_and_outside_tests_pass(tmp_path: Path) -> None:
    write(
        tmp_path, "tests/unit/shared/test_a.py", "import pytest\nimport tests.support"
    )
    write(tmp_path, "tests/conftest.py", "from tests.support.clock import FakeClock")
    write(tmp_path, "tests/support/batches.py", "from tests.support import identity")
    write(tmp_path, "tests/support/processes/jobs.py", "from ..clock import FakeClock")

    assert import_errors(tmp_path) == []

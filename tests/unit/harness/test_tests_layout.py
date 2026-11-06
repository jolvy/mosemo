"""tests/ 구조 규칙: 최상위 항목(H1), 분류 아래 첫 폴더(H2), `__init__.py` 위치(H3)와
테스트 코드 사이 import 방향(H4)."""

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

TEST_MODULES = ("tests.unit", "tests.integration", "tests.e2e")
TOP_LEVEL_FILES = {"__init__.py", "conftest.py", "AGENTS.md", "CLAUDE.md"}
TOP_LEVEL_FOLDERS = {"support", "unit", "integration", "e2e"}
CATEGORY_FOLDERS = ("unit", "integration")
NON_PACKAGE_FOLDERS = {"harness", "scripts", "migrations", "support"}


def ignored(name: str) -> bool:
    return name == "__pycache__" or name.startswith(".")


def python_files(root: Path) -> list[Path]:
    tests = root / "tests"
    return sorted(
        path
        for path in tests.rglob("*.py")
        if not any(ignored(part) for part in path.relative_to(tests).parts)
    )


def entries(folder: Path) -> tuple[list[Path], list[Path]]:
    """`.py` 파일을 가진 하위 폴더와 파일을 나눈다. 무시할 항목은 뺀다."""
    files, folders = [], []
    for path in sorted(folder.iterdir()):
        if ignored(path.name):
            continue
        if path.is_file():
            files.append(path)
        elif has_python_file(path):
            folders.append(path)
    return files, folders


def has_python_file(folder: Path) -> bool:
    return any(
        not any(ignored(part) for part in path.relative_to(folder).parts)
        for path in folder.rglob("*.py")
    )


def top_level_errors(root: Path) -> list[str]:
    files, folders = entries(root / "tests")
    return [
        f"{path.relative_to(root)}: tests/ 최상위에 둘 수 없는 항목이다"
        for path in [
            *(path for path in files if path.name not in TOP_LEVEL_FILES),
            *(path for path in folders if path.name not in TOP_LEVEL_FOLDERS),
        ]
    ]


def source_packages(root: Path) -> set[str]:
    _, folders = entries(root / "src/mosemo")
    return {path.name for path in folders}


def category_folder_errors(root: Path) -> list[str]:
    allowed = source_packages(root) | NON_PACKAGE_FOLDERS
    errors = []
    for category in CATEGORY_FOLDERS:
        folder = root / "tests" / category
        if not folder.is_dir():
            continue
        files, folders = entries(folder)
        errors.extend(
            f"{path.relative_to(root)}: 테스트 파일은 tests/{category}/ 아래 "
            "패키지 폴더에 둔다"
            for path in files
            if path.name.startswith("test_") and path.suffix == ".py"
        )
        errors.extend(
            f"{path.relative_to(root)}: tests/{category}/의 첫 폴더는 "
            "src/mosemo 최상위 패키지 또는 harness·scripts·migrations·support다"
            for path in folders
            if path.name not in allowed
        )
    return errors


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


def test_repository_tests_top_level_has_only_allowed_entries(
    repository: Path,
) -> None:
    assert top_level_errors(repository) == []


def test_repository_category_folders_follow_packages(repository: Path) -> None:
    assert category_folder_errors(repository) == []


@pytest.mark.parametrize(
    "relative",
    [
        "tests/component/labels/test_a.py",
        "tests/batch_support.py",
        "tests/README.md",
    ],
)
def test_unknown_tests_top_level_entry_is_rejected(
    tmp_path: Path, relative: str
) -> None:
    write(tmp_path, "tests/conftest.py")
    write(tmp_path, relative)

    reported = "/".join(Path(relative).parts[:2])

    assert top_level_errors(tmp_path) == [
        f"{reported}: tests/ 최상위에 둘 수 없는 항목이다"
    ]


def test_allowed_top_level_entries_and_stale_cache_folders_pass(
    tmp_path: Path,
) -> None:
    for relative in [
        "tests/__init__.py",
        "tests/conftest.py",
        "tests/AGENTS.md",
        "tests/CLAUDE.md",
        "tests/support/clock.py",
        "tests/unit/shared/test_a.py",
        "tests/integration/conftest.py",
        "tests/e2e/test_b.py",
        "tests/component/__pycache__/test_c.cpython-314.pyc",
        "tests/component/labels/__pycache__/test_d.py",
        "tests/.pytest_cache/README.md",
        "tests/__pycache__/conftest.cpython-314.pyc",
    ]:
        write(tmp_path, relative)

    assert top_level_errors(tmp_path) == []


@pytest.mark.parametrize(
    ("relative", "message"),
    [
        (
            "tests/unit/test_a.py",
            "테스트 파일은 tests/unit/ 아래 패키지 폴더에 둔다",
        ),
        (
            "tests/integration/test_a.py",
            "테스트 파일은 tests/integration/ 아래 패키지 폴더에 둔다",
        ),
        (
            "tests/unit/accounts/test_a.py",
            (
                "tests/unit/의 첫 폴더는 src/mosemo 최상위 패키지 또는 "
                "harness·scripts·migrations·support다"
            ),
        ),
        (
            "tests/integration/missing/sub/test_a.py",
            (
                "tests/integration/의 첫 폴더는 src/mosemo 최상위 패키지 또는 "
                "harness·scripts·migrations·support다"
            ),
        ),
    ],
)
def test_category_folder_outside_packages_is_rejected(
    tmp_path: Path, relative: str, message: str
) -> None:
    write(tmp_path, "src/mosemo/labels/__init__.py")
    write(tmp_path, relative)
    reported = "/".join(Path(relative).parts[:3])

    assert category_folder_errors(tmp_path) == [f"{reported}: {message}"]


def test_category_folders_of_packages_and_stale_cache_folders_pass(
    tmp_path: Path,
) -> None:
    write(tmp_path, "src/mosemo/labels/__init__.py")
    write(tmp_path, "src/mosemo/__pycache__/x.py")
    for relative in [
        "tests/unit/labels/test_a.py",
        "tests/unit/harness/test_b.py",
        "tests/unit/scripts/test_c.py",
        "tests/unit/support/test_d.py",
        "tests/integration/conftest.py",
        "tests/integration/migrations/test_e.py",
        "tests/integration/labels/sub/test_f.py",
        "tests/unit/accounts/__pycache__/test_g.cpython-314.pyc",
        "tests/unit/devices/__pycache__/test_h.py",
        "tests/unit/__pycache__/test_i.cpython-314.pyc",
        "tests/unit/.hidden/test_j.py",
    ]:
        write(tmp_path, relative)

    assert category_folder_errors(tmp_path) == []


def test_source_packages_skip_ignored_and_pyc_only_folders(tmp_path: Path) -> None:
    for relative in [
        "src/mosemo/__init__.py",
        "src/mosemo/labels/__init__.py",
        "src/mosemo/identity/domain/model.py",
        "src/mosemo/__pycache__/x.py",
        "src/mosemo/.hidden/x.py",
        "src/mosemo/old/__pycache__/x.cpython-314.pyc",
        "src/mosemo/stale/__pycache__/x.py",
    ]:
        write(tmp_path, relative)

    assert source_packages(tmp_path) == {"labels", "identity"}


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

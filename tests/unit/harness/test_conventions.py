import ast
from pathlib import Path
from types import ModuleType

import pytest

ALLOWED_PYPROJECT = """
[tool.ruff.lint]
extend-select = ["I", "TID251", "DTZ"]

[tool.ruff.lint.per-file-ignores]
"src/mosemo/clock.py" = ["TID251"]
"""


@pytest.mark.parametrize(
    "source",
    ["Service(clock=lambda: now)", "build(session, clock=lambda: fake_clock.now())"],
)
def test_test_clock_argument_rejects_lambda(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_test_clock_lambdas(ast.parse(source))
    assert errors and "CS-P23" in errors[0]


@pytest.mark.parametrize(
    "source",
    ["Service(clock=fake_clock)", "Service(clock=utc_now)", "f(now=lambda: 1)"],
)
def test_test_clock_argument_allows_clock_objects(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_test_clock_lambdas(ast.parse(source)) == []


@pytest.mark.parametrize(
    "source",
    [
        "app.dependency_overrides[get_clock] = lambda: lambda: now",
        "app.dependency_overrides[deps.get_clock] = lambda: lambda: now",
        "app.dependency_overrides.update({get_clock: lambda: lambda: now})",
        "app.dependency_overrides.update({deps.get_clock: lambda: lambda: now})",
    ],
)
def test_get_clock_override_rejects_lambda_returning_lambda(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_clock_overrides(ast.parse(source))
    assert errors and "CS-P23" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "app.dependency_overrides[get_clock] = lambda: fake_clock",
        "app.dependency_overrides.update({get_clock: lambda: fake_clock})",
        "app.dependency_overrides[get_session] = lambda: lambda: now",
    ],
)
def test_get_clock_override_allows_clock_object(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_clock_overrides(ast.parse(source)) == []


@pytest.mark.parametrize(
    "comment",
    [
        "# noqa",
        "# NOQA",
        "# noqa:",
        "#noqa:TID251",
        "# noqa: TID251",
        "# noqa : tid251",
        "# noqa: E501, TID251",
        "# noqa: E501 DTZ005",
        "# noqa: DTZ",
        "# ruff: noqa",
        "#ruff:noqa: F401",
    ],
)
def test_suppression_comment_rejects_blanket_and_clock_codes(
    conventions_validator: ModuleType, comment: str
) -> None:
    errors = conventions_validator.check_suppression_comments(f"x = 1  {comment}\n")
    assert errors and "CS-P15" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "import os  # noqa: F401\n",
        "x = 1  # noqa: E501, F401\n",
        "pass  # noqa: BLE001 - safe process boundary\n",
        "pass  # noqa: BLE001 - TID251 설명은 대상 아님\n",
        'text = "# noqa"\n',
        'text = "# ruff: noqa  # noqa: TID251"\n',
        "x = 1  # xnoqa\n",
    ],
)
def test_suppression_comment_allows_specific_codes_and_strings(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_suppression_comments(source) == []


def test_ruff_config_allows_only_clock_exception(
    conventions_validator: ModuleType,
) -> None:
    assert conventions_validator.check_ruff_config(ALLOWED_PYPROJECT) == []


@pytest.mark.parametrize(
    "extra",
    [
        'ignore = ["TID"]',
        'ignore = ["TID2"]',
        'extend-ignore = ["TID25"]',
        'ignore = ["DTZ005"]',
        'ignore = ["ALL"]',
    ],
)
def test_ruff_config_rejects_prefix_dtz_and_all(
    conventions_validator: ModuleType, extra: str
) -> None:
    pyproject = ALLOWED_PYPROJECT.replace(
        "[tool.ruff.lint]\n", f"[tool.ruff.lint]\n{extra}\n"
    )
    errors = conventions_validator.check_ruff_config(pyproject)
    assert errors and "CS-P15" in errors[0]


@pytest.mark.parametrize(
    "pyproject",
    [
        ALLOWED_PYPROJECT.replace('["TID251"]', '["TID251", "DTZ005"]'),
        ALLOWED_PYPROJECT.replace("src/mosemo/clock.py", "src/mosemo/other.py"),
        ALLOWED_PYPROJECT
        + '\n[tool.ruff.lint.extend-per-file-ignores]\n"src/mosemo/clock.py" = ["TID251"]\n',
        ALLOWED_PYPROJECT.replace(
            "[tool.ruff.lint]", '[tool.ruff]\nexclude = ["x"]\n\n[tool.ruff.lint]'
        ),
        ALLOWED_PYPROJECT.replace(
            "[tool.ruff.lint]",
            '[tool.ruff]\nextend-exclude = ["x"]\n\n[tool.ruff.lint]',
        ),
        ALLOWED_PYPROJECT.replace("extend-select", 'exclude = ["x"]\nextend-select'),
    ],
)
def test_ruff_config_rejects_other_ignores_and_excludes(
    conventions_validator: ModuleType, pyproject: str
) -> None:
    errors = conventions_validator.check_ruff_config(pyproject)
    assert errors and "CS-P15" in errors[0]


@pytest.mark.parametrize("name", ["ruff.toml", ".ruff.toml", "src/ruff.toml"])
def test_validate_rejects_other_ruff_config_files(
    conventions_validator: ModuleType, tmp_path: Path, name: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(ALLOWED_PYPROJECT)
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("")
    errors = conventions_validator.validate(tmp_path)
    assert errors and errors[0].startswith(name) and "CS-P15" in errors[0]


@pytest.mark.parametrize(
    "signature",
    [
        "self, clock",
        "self, clock, /",
        "self, session, clock: Clock",
        "self, *, clock: Clock = utc_now",
        "self, *, clock: Clock | None = None",
    ],
)
def test_init_clock_rejects_positional_or_default(
    conventions_validator: ModuleType, signature: str
) -> None:
    source = f"class Service:\n    def __init__({signature}):\n        pass\n"
    errors = conventions_validator.check_init_clock(ast.parse(source))
    assert errors and "CS-P15" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "class S:\n    def __init__(self, session, *, clock: Clock):\n        pass\n",
        "class S:\n    def __init__(self, *, now=None, clock):\n        pass\n",
        "def get_service(clock: ClockDep = None):\n    pass\n",
    ],
)
def test_init_clock_allows_required_keyword_only_and_dependencies(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_init_clock(ast.parse(source)) == []


def test_validate_scopes_rules_and_reports_location(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(ALLOWED_PYPROJECT)
    files = {
        "tests/test_x.py": "Service(clock=lambda: now)\n",
        "src/mosemo/x.py": "class S:\n    def __init__(self, clock):\n        pass\n",
        "scripts/x.py": "Service(clock=lambda: now)  # noqa\n",
        ".venv/lib/x.py": "x = 1  # noqa\n",
    }
    for name, source in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(source)
    errors = conventions_validator.validate(tmp_path)
    assert [error.split(": ", 1)[0] for error in errors] == [
        "scripts/x.py:1",
        "src/mosemo/x.py:2",
        "tests/test_x.py:1",
    ]


def test_real_repository_satisfies_conventions(
    conventions_validator: ModuleType,
) -> None:
    assert conventions_validator.validate() == []

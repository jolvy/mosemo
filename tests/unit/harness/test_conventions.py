import ast
from pathlib import Path
from types import ModuleType

import pytest

ALLOWED_PYPROJECT = """
[tool.ruff]
line-length = 88
target-version = "py314"

[tool.ruff.lint]
extend-select = ["I", "TID251", "DTZ", "RET505", "SIM102", "PLR5501", "SIM103", "PLR0917",
                 "FBT001", "FBT003", "PLR2004", "N", "FAST002", "ASYNC", "ERA001", "S110", "SIM105", "PT",
                 "PLR0913"]

[tool.ruff.lint.per-file-ignores]
"src/mosemo/infra/clock.py" = ["TID251"]
"migrations/env.py" = ["F401"]
"src/mosemo/api/middleware.py" = ["PLR0917"]
"tests/**" = ["PLR2004", "FBT", "ASYNC", "PLR0917", "PLR0913"]
"scripts/**" = ["PLR2004"]
"migrations/**" = ["PLR2004"]
".codex/**" = ["PLR2004"]
"src/**" = ["BLE001"]
"src/**/models.py" = ["PLR0913"]

[tool.ruff.lint.pylint]
max-positional-args = 2

[tool.ruff.lint.pep8-naming]
extend-ignore-names = ["*Exception"]

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"datetime.datetime.now".msg = "CS-P15: 현재 시각은 `mosemo.infra.clock`의 clock을 주입받아 얻는다."
"datetime.datetime.utcnow".msg = "CS-P15: 현재 시각은 `mosemo.infra.clock`의 clock을 주입받아 얻는다."
"datetime.datetime.today".msg = "CS-P15: 현재 시각은 `mosemo.infra.clock`의 clock을 주입받아 얻는다."
"datetime.date.today".msg = "CS-P15: 오늘 날짜는 `mosemo.infra.clock`의 clock을 주입받아 얻은 시각에서 계산한다."
"time.time".msg = "CS-P15: 현재 시각은 `mosemo.infra.clock`의 clock을 주입받아 얻는다."
"time.time_ns".msg = "CS-P15: 현재 시각은 `mosemo.infra.clock`의 clock을 주입받아 얻는다."

[tool.ruff.format]
indent-style = "space"
line-ending = "auto"
quote-style = "double"
"""


def rule_errors(errors: list[str], *rule_ids: str) -> list[str]:
    return [error for error in errors if any(rule in error for rule in rule_ids)]


@pytest.mark.parametrize(
    "source",
    ["Service(clock=lambda: now)", "build(session, clock=lambda: fake_clock.now())"],
)
def test_test_clock_argument_rejects_lambda(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_test_clock_lambdas(ast.parse(source))
    assert errors
    assert "CS-P23" in errors[0]


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
    assert errors
    assert "CS-P23" in errors[0]


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
        "# noqa: DTZ",
        "# noqa: F401",
        "# noqa: BLE001 - safe process boundary",
        "# ruff: noqa",
        "#ruff:noqa: F401",
        "# type: ignore",
        "# type: ignore[assignment]",
        "# TYPE : IGNORE",
        "# ty: ignore",
        "# ty: ignore[invalid-assignment]",
        "# pyright: ignore",
        "# complexipy: ignore",
        "# isort: skip_file",
        "# isort:skip",
        "# fmt: off",
        "# fmt: on",
        "# fmt: skip",
        "# pragma: no cover",
        "# pragma:  NO   cover",
        "# 설명  # noqa: E501",
    ],
)
def test_suppression_comment_rejects_directives(
    conventions_validator: ModuleType, comment: str
) -> None:
    errors = conventions_validator.check_suppression_comments(f"x = 1  {comment}\n")
    assert errors
    assert errors[0].startswith("1: CS-P22")


@pytest.mark.parametrize(
    "source",
    [
        'text = "# noqa"\n',
        'text = "# ruff: noqa  # noqa: TID251  # type: ignore  # fmt: off"\n',
        "x = 1  # xnoqa\n",
        "x = 1  # noqa_like name\n",
        "x = 1  # 설명noqa중\n",
        "x = 1  # mytype: ignore 아님\n",
        "x = 1  # 넓은 except는 프로세스 경계다\n",
        "x: int = 1  # type annotation\n",
    ],
)
def test_suppression_comment_allows_strings_and_partial_words(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_suppression_comments(source) == []


def test_ruff_config_allows_exact_config(
    conventions_validator: ModuleType,
) -> None:
    assert conventions_validator.check_ruff_config(ALLOWED_PYPROJECT) == []


@pytest.mark.parametrize(
    "pyproject",
    [
        ALLOWED_PYPROJECT.replace(
            '"src/**" = ["BLE001"]',
            '"src/**" = ["BLE001"]\n"src/mosemo/x.py" = ["E501"]',
        ),
        ALLOWED_PYPROJECT.replace(
            '"scripts/**" = ["PLR2004"]', '"scripts/**" = ["PLR2004", "N"]'
        ),
        ALLOWED_PYPROJECT.replace('["TID251"]', '["TID251", "DTZ005"]'),
        ALLOWED_PYPROJECT.replace("src/mosemo/infra/clock.py", "src/mosemo/other.py"),
        ALLOWED_PYPROJECT.replace('"S110", ', ""),
        ALLOWED_PYPROJECT.replace('"PT",\n', '"PT", "E501",\n'),
        ALLOWED_PYPROJECT.replace(',\n                 "PLR0913"]', "]"),
        ALLOWED_PYPROJECT.replace('"PLR0917", "PLR0913"]', '"PLR0917"]'),
        ALLOWED_PYPROJECT.replace('"src/**/models.py" = ["PLR0913"]\n', ""),
        ALLOWED_PYPROJECT.replace("max-positional-args = 2", "max-positional-args = 5"),
        ALLOWED_PYPROJECT.replace(
            "[tool.ruff.lint]\n", '[tool.ruff.lint]\nignore = ["PLR2004"]\n'
        ),
        ALLOWED_PYPROJECT.replace(
            "[tool.ruff.lint]\n", '[tool.ruff.lint]\nextend-ignore = ["N"]\n'
        ),
        ALLOWED_PYPROJECT.replace(
            "[tool.ruff.lint]\n", '[tool.ruff.lint]\nselect = ["E"]\n'
        ),
        ALLOWED_PYPROJECT.replace(
            "[tool.ruff.lint]\n", "[tool.ruff.lint]\npreview = true\n"
        ),
        ALLOWED_PYPROJECT.replace(
            'target-version = "py314"\n',
            'target-version = "py314"\nextend = "../x.toml"\n',
        ),
        ALLOWED_PYPROJECT.replace(
            'target-version = "py314"\n', 'target-version = "py314"\nexclude = ["x"]\n'
        ),
        ALLOWED_PYPROJECT.replace(
            'target-version = "py314"\n',
            'target-version = "py314"\nextend-exclude = ["x"]\n',
        ),
        ALLOWED_PYPROJECT.replace(
            'target-version = "py314"\n', 'target-version = "py314"\nignore = ["DTZ"]\n'
        ),
        ALLOWED_PYPROJECT
        + '\n[tool.ruff.lint.extend-per-file-ignores]\n"src/x.py" = ["N"]\n',
        ALLOWED_PYPROJECT.replace('"time.time_ns".msg', '"time.monotonic".msg'),
        ALLOWED_PYPROJECT.replace('quote-style = "double"', 'exclude = ["x"]'),
        "[tool.other]\nx = 1\n",
    ],
)
def test_ruff_config_rejects_any_difference(
    conventions_validator: ModuleType, pyproject: str
) -> None:
    errors = conventions_validator.check_ruff_config(pyproject)
    assert errors
    assert all(error.startswith("pyproject.toml: CS-P22") for error in errors)


@pytest.mark.parametrize("name", ["ruff.toml", ".ruff.toml", "src/ruff.toml"])
def test_validate_rejects_other_ruff_config_files(
    conventions_validator: ModuleType, tmp_path: Path, name: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(ALLOWED_PYPROJECT)
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("")
    errors = rule_errors(conventions_validator.validate(tmp_path), "CS-P22")
    assert len(errors) == 1
    assert errors[0].startswith(f"{name}: CS-P22")


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
    assert errors
    assert "CS-P15" in errors[0]


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
    errors = rule_errors(
        conventions_validator.validate(tmp_path), "CS-P15", "CS-P22", "CS-P23"
    )
    assert [error.split(": ", 1)[0] for error in errors] == [
        "scripts/x.py:1",
        "src/mosemo/x.py:2",
        "tests/test_x.py:1",
    ]


def test_real_repository_satisfies_conventions(
    conventions_validator: ModuleType,
) -> None:
    assert conventions_validator.validate() == []


def repository_class(method: str) -> str:
    return f"class Repo:\n    async def {method}(self):\n        pass\n"


@pytest.mark.parametrize("method", ["save", "insert", "get_by_id", "count", "upsert"])
def test_repository_method_names_reject_unlisted_verbs(
    conventions_validator: ModuleType, method: str
) -> None:
    errors = conventions_validator.check_repository_method_names(
        ast.parse(repository_class(method))
    )
    assert errors
    assert "CS-P10" in errors[0]


@pytest.mark.parametrize(
    "method",
    [
        "find",
        "find_by_id",
        "list_owned",
        "has_device",
        "is_confirmed",
        "add",
        "add_all",
        "add_if_absent",
        "delete",
        "delete_expired",
        "replace_labels",
        "lock_timeline",
        "claim",
        "_build_query",
        "__init__",
    ],
)
def test_repository_method_names_allow_listed_verbs_and_private(
    conventions_validator: ModuleType, method: str
) -> None:
    assert (
        conventions_validator.check_repository_method_names(
            ast.parse(repository_class(method))
        )
        == []
    )


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    pass\nexcept IntegrityError:\n    pass\n",
        "try:\n    pass\nexcept sqlalchemy.exc.IntegrityError:\n    pass\n",
        (
            "from sqlalchemy.exc import IntegrityError as Duplicate\n"
            "try:\n    pass\nexcept Duplicate:\n    pass\n"
        ),
        "try:\n    pass\nexcept (ValueError, IntegrityError) as error:\n    pass\n",
    ],
)
def test_integrity_error_handler_is_rejected(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_integrity_error_handling(ast.parse(source))
    assert errors
    assert "CS-P26" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    pass\nexcept DBAPIError:\n    pass\n",
        "try:\n    pass\nexcept (ValueError, KeyError):\n    pass\n",
        "try:\n    pass\nexcept:\n    raise\n",
    ],
)
def test_other_exception_handlers_are_allowed(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_integrity_error_handling(ast.parse(source)) == []


def test_add_if_absent_requires_on_conflict_do_nothing(
    conventions_validator: ModuleType,
) -> None:
    source = (
        "class Repo:\n"
        "    async def add_if_absent(self, row):\n"
        "        self._session.add(row)\n"
    )
    errors = conventions_validator.check_add_if_absent_conflict(ast.parse(source))
    assert errors
    assert "CS-P26" in errors[0]


def test_add_if_absent_allows_on_conflict_do_nothing(
    conventions_validator: ModuleType,
) -> None:
    source = (
        "class Repo:\n"
        "    async def add_if_absent(self, values):\n"
        "        statement = insert(Row).values(values).on_conflict_do_nothing()\n"
        "        return await self._session.scalar(statement)\n"
    )
    assert conventions_validator.check_add_if_absent_conflict(ast.parse(source)) == []


@pytest.mark.parametrize(
    "source",
    [
        "await self._session.scalar(statement)",
        "await self.session.execute(statement)",
        "await session.get(Device, device_id)",
        "session.add(row)",
    ],
)
def test_session_calls_outside_repository_are_rejected(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_session_calls(ast.parse(source))
    assert errors
    assert "CS-P25" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "self._session.begin()",
        "await self._session.flush()",
        "await session.commit()",
        "await self.session.rollback()",
        "self._session_factory()",
        "self.sessions()",
    ],
)
def test_session_transaction_calls_are_allowed(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_session_calls(ast.parse(source)) == []


@pytest.mark.parametrize(
    "source",
    [
        "from sqlalchemy import select",
        "from sqlalchemy import MetaData, func",
        "from sqlalchemy.dialects.postgresql import insert",
        "import sqlalchemy",
        "import sqlalchemy as sa",
    ],
)
def test_sql_construct_imports_are_rejected(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_sql_imports(ast.parse(source))
    assert errors
    assert "CS-P25" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker",
        "from sqlalchemy.exc import DBAPIError",
        "from sqlalchemy import MetaData",
        "from sqlalchemy.types import String, TypeDecorator",
        "from sqlalchemy.dialects.postgresql import JSONB",
        "from .sqlalchemy import select",
    ],
)
def test_session_types_and_exception_imports_are_allowed(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_sql_imports(ast.parse(source)) == []


def test_validate_scopes_repository_rules_to_src(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(ALLOWED_PYPROJECT)
    service = (
        "from sqlalchemy import select\n"
        "async def run(session):\n"
        "    await session.scalar(select(1))\n"
    )
    outside = service + "import sqlalchemy\nsession.add(row)\n"
    files = {
        "src/mosemo/x/repository.py": (
            "from sqlalchemy import select\n"
            "class XRepository:\n"
            "    async def find(self):\n"
            "        return await self._session.scalar(select(1))\n"
            "    async def save(self, row):\n"
            "        self._session.add(row)\n"
        ),
        "src/mosemo/x/models.py": "from sqlalchemy import func\n",
        "src/mosemo/x/service.py": service,
        "tests/integration/test_x_repository.py": outside
        + "class Repo:\n    def save(self):\n        pass\n",
        "migrations/versions/x.py": outside,
        "scripts/x.py": outside,
    }
    for name, source in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(source)
    errors = rule_errors(conventions_validator.validate(tmp_path), "CS-P10", "CS-P25")
    assert [error.split(": ", 2)[:2] for error in errors] == [
        ["src/mosemo/x/repository.py:5", "CS-P10"],
        ["src/mosemo/x/service.py:3", "CS-P25"],
        ["src/mosemo/x/service.py:1", "CS-P25"],
    ]


def test_validate_rejects_transaction_calls_outside_boundary_files(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(ALLOWED_PYPROJECT)
    transaction = (
        "async def run(session):\n"
        "    async with session.begin():\n"
        "        await session.flush()\n"
        "    await session.commit()\n"
        "    await session.rollback()\n"
    )
    files = {
        "src/mosemo/x/repository.py": (
            "async def run(self):\n    async with self._session.begin():\n        pass\n"
        ),
        "src/mosemo/x/router.py": "async def run(session):\n    await session.commit()\n",
        "src/mosemo/x/mappers.py": "session.rollback()\n",
        "src/mosemo/x/queries.py": (
            "async def run(self):\n"
            "    async with self._session_factory.begin_nested():\n"
            "        pass\n"
        ),
        "src/mosemo/x/service.py": transaction,
        "src/mosemo/x/executor.py": transaction,
        "src/mosemo/x/processing.py": transaction,
        "src/mosemo/infra/transactions.py": transaction,
        "src/mosemo/y/transactions.py": transaction,
        "src/mosemo/y/repository.py": "async def run(session):\n    await session.flush()\n",
        "scripts/x.py": "session.commit()\n",
        "tests/x.py": "session.commit()\n",
    }
    for name, source in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(source)
    errors = rule_errors(conventions_validator.validate(tmp_path), "CS-P13")
    assert sorted(error.split(": ", 2)[:2] for error in errors) == [
        ["src/mosemo/x/mappers.py:1", "CS-P13"],
        ["src/mosemo/x/queries.py:2", "CS-P13"],
        ["src/mosemo/x/repository.py:2", "CS-P13"],
        ["src/mosemo/x/router.py:2", "CS-P13"],
        ["src/mosemo/y/transactions.py:2", "CS-P13"],
        ["src/mosemo/y/transactions.py:4", "CS-P13"],
        ["src/mosemo/y/transactions.py:5", "CS-P13"],
    ]
    assert "`service.py`·`executor.py`·`processing.py`에서만 연다" in errors[0]


def test_validate_keeps_session_rule_for_nested_transaction_in_service(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(ALLOWED_PYPROJECT)
    service = tmp_path / "src/mosemo/x/service.py"
    service.parent.mkdir(parents=True)
    service.write_text(
        "async def run(self):\n"
        "    async with self._session.begin_nested():\n"
        "        pass\n"
    )
    errors = conventions_validator.validate(tmp_path)
    assert rule_errors(errors, "CS-P13") == []
    assert [error.split(": ", 2)[:2] for error in rule_errors(errors, "CS-P25")] == [
        ["src/mosemo/x/service.py:2", "CS-P25"]
    ]


ALLOWED_BROAD_EXCEPTS = {
    "src/mosemo/consumer/__main__.py": (
        "def main():\n    try:\n        run()\n    except Exception:\n        exit(1)\n"
    ),
    "src/mosemo/batch/__main__.py": (
        "def main():\n"
        "    try:\n"
        "        run()\n"
        "    except Exception, asyncio.CancelledError:\n"
        "        exit(1)\n"
    ),
    "src/mosemo/infra/messaging/subscriber.py": (
        "class MessageConsumer:\n"
        "    async def handle(self):\n"
        "        try:\n"
        "            pass\n"
        "        except Exception as error:\n"
        "            raise error\n"
    ),
    "src/mosemo/identity/presentation/auth/router.py": (
        "def create_router():\n"
        "    async def callback():\n"
        "        try:\n"
        "            pass\n"
        "        except Exception:\n"
        "            return redirect()\n"
    ),
    "src/mosemo/labeling/application/proposals.py": (
        "class ProposeLabel:\n"
        "    async def execute(self):\n"
        "        try:\n"
        "            pass\n"
        "        except Exception:\n"
        "            record()\n"
    ),
    "src/mosemo/infra/scheduling.py": (
        "async def execute_job():\n    try:\n        pass\n    except Exception:\n"
        "        log()\n"
    ),
}


def broad_except_trees(**changes: str) -> dict[str, ast.AST]:
    sources = {**ALLOWED_BROAD_EXCEPTS, **changes}
    return {path: ast.parse(source) for path, source in sources.items()}


def test_broad_excepts_allow_registered_boundaries(
    conventions_validator: ModuleType,
) -> None:
    trees = broad_except_trees(
        **{
            "src/mosemo/x.py": (
                "def run():\n    try:\n        pass\n    except ValueError:\n"
                "        pass\n    except (KeyError, TypeError):\n        pass\n"
            )
        }
    )
    assert conventions_validator.check_broad_excepts(trees) == []


@pytest.mark.parametrize(
    "handler",
    [
        "except Exception:",
        "except BaseException:",
        "except builtins.Exception:",
        "except (ValueError, Exception):",
        "except ValueError, BaseException:",
        "except:",
        "except* Exception:",
    ],
)
def test_broad_excepts_reject_unlisted_function(
    conventions_validator: ModuleType, handler: str
) -> None:
    source = f"def run():\n    try:\n        pass\n    {handler}\n        pass\n"
    trees = broad_except_trees(**{"src/mosemo/x.py": source})
    errors = conventions_validator.check_broad_excepts(trees)
    assert len(errors) == 1
    assert errors[0].startswith("src/mosemo/x.py:4: CS-P18")
    assert "`run`" in errors[0]


def test_broad_excepts_reject_count_over_limit(
    conventions_validator: ModuleType,
) -> None:
    source = (
        "async def execute_job():\n"
        "    try:\n        pass\n    except Exception:\n        log()\n"
        "    try:\n        pass\n    except Exception:\n        log()\n"
    )
    trees = broad_except_trees(**{"src/mosemo/infra/scheduling.py": source})
    errors = conventions_validator.check_broad_excepts(trees)
    assert len(errors) == 1
    assert errors[0].startswith("src/mosemo/infra/scheduling.py:8: CS-P18")


def test_broad_excepts_use_enclosing_class_and_function_names(
    conventions_validator: ModuleType,
) -> None:
    source = (
        "class Other:\n"
        "    def handle(self):\n"
        "        try:\n            pass\n        except Exception:\n            pass\n"
    )
    trees = broad_except_trees(**{"src/mosemo/infra/messaging/subscriber.py": source})
    errors = conventions_validator.check_broad_excepts(trees)
    assert [error.split(": ", 2)[:2] for error in errors] == [
        ["src/mosemo/infra/messaging/subscriber.py:5", "CS-P18"],
        ["src/mosemo/infra/messaging/subscriber.py", "CS-P18"],
    ]
    assert "`Other.handle`" in errors[0]
    assert "`MessageConsumer.handle`" in errors[1]


def test_broad_excepts_reject_listed_function_without_handler(
    conventions_validator: ModuleType,
) -> None:
    trees = broad_except_trees(
        **{
            "src/mosemo/identity/presentation/auth/router.py": (
                "def create_router():\n    async def callback():\n        pass\n"
            )
        }
    )
    errors = conventions_validator.check_broad_excepts(trees)
    assert len(errors) == 1
    assert errors[0].startswith(
        "src/mosemo/identity/presentation/auth/router.py: CS-P18"
    )


def test_complexity_snapshot_rejects_existing_file(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    assert conventions_validator.check_complexity_snapshot(tmp_path) == []
    (tmp_path / "complexipy-snapshot.json").write_text("[]")
    errors = conventions_validator.check_complexity_snapshot(tmp_path)
    assert len(errors) == 1
    assert errors[0].startswith("complexipy-snapshot.json: CS-P01")


def test_complexity_config_rejects_tool_section(
    conventions_validator: ModuleType,
) -> None:
    assert conventions_validator.check_complexity_config(ALLOWED_PYPROJECT) == []
    errors = conventions_validator.check_complexity_config(
        ALLOWED_PYPROJECT + "\n[tool.complexipy]\nmax-complexity-allowed = 30\n"
    )
    assert len(errors) == 1
    assert errors[0].startswith("pyproject.toml: CS-P01")


def test_validate_checks_complexity_files(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(ALLOWED_PYPROJECT)
    (tmp_path / "complexipy.toml").write_text("")
    (tmp_path / "complexipy-snapshot.json").write_text("[]")
    errors = rule_errors(conventions_validator.validate(tmp_path), "CS-P01")
    assert [error.split(": ", 2)[:2] for error in errors] == [
        ["complexipy-snapshot.json", "CS-P01"],
        ["complexipy.toml", "CS-P01"],
    ]


EXCEPTIONS_SOURCE = (
    "class ApiException(Exception):\n    pass\n"
    "class NotFoundException(ApiException):\n    pass\n"
    "class GoneException(NotFoundException):\n    pass\n"
)


def test_exception_classes_allow_api_exception_hierarchy(
    conventions_validator: ModuleType,
) -> None:
    errors = conventions_validator.check_exception_classes(
        ast.parse(EXCEPTIONS_SOURCE + "class DomainError(Exception):\n    pass\n"),
        in_exceptions_file=True,
    )
    assert errors == []


@pytest.mark.parametrize(
    ("source", "in_exceptions_file"),
    [
        ("class FooException(Exception):\n    pass\n", False),
        ("class NotFoundException(ApiException):\n    pass\n", False),
        (EXCEPTIONS_SOURCE + "class FooException(Exception):\n    pass\n", True),
        (EXCEPTIONS_SOURCE + "class FooException(ValueError):\n    pass\n", True),
    ],
)
def test_exception_classes_reject_names_outside_api_hierarchy(
    conventions_validator: ModuleType, source: str, in_exceptions_file: bool
) -> None:
    errors = conventions_validator.check_exception_classes(
        ast.parse(source), in_exceptions_file=in_exceptions_file
    )
    assert len(errors) == 1
    assert "CS-P07" in errors[0]
    assert "FooException" in errors[0] or "NotFoundException" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "from mosemo.exceptions import ApiException, ErrorCode\n",
        "from mosemo.exceptions import NotFoundException\n",
        "raise exceptions.ApiException(ErrorCode.X)\n",
        "raise GoneException(ErrorCode.X)\n",
        "raise exceptions.NotFoundException\n",
    ],
)
def test_api_exception_usage_rejects_import_and_construction(
    conventions_validator: ModuleType, source: str
) -> None:
    names = conventions_validator.api_exception_names(ast.parse(EXCEPTIONS_SOURCE))
    errors = conventions_validator.check_api_exception_usage(ast.parse(source), names)
    assert len(errors) == 1
    assert errors[0].startswith("1: CS-P07")


def test_api_exception_usage_allows_error_codes_and_domain_errors(
    conventions_validator: ModuleType,
) -> None:
    source = "from mosemo.exceptions import ErrorCode\nraise DomainError(ErrorCode.X)\n"
    assert (
        conventions_validator.check_api_exception_usage(
            ast.parse(source), {"ApiException"}
        )
        == []
    )


def test_validate_scopes_exception_rules(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(ALLOWED_PYPROJECT)
    usage = "from mosemo.shared.errors import ApiException\nraise ApiException(code)\n"
    files = {
        "src/mosemo/shared/errors.py": EXCEPTIONS_SOURCE,
        "src/mosemo/x/router.py": usage,
        "src/mosemo/api/dependencies.py": usage,
        "src/mosemo/api/exception_handlers.py": usage,
        "src/mosemo/x/service.py": "from mosemo.shared.errors import GoneException\n",
        "tests/test_x.py": usage + "class FakeException(Exception):\n    pass\n",
    }
    for name, source in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(source)
    errors = rule_errors(conventions_validator.validate(tmp_path), "CS-P07")
    assert sorted(error.split(": ", 2)[:2] for error in errors) == [
        ["src/mosemo/x/service.py:1", "CS-P07"],
        ["tests/test_x.py:3", "CS-P07"],
    ]


@pytest.mark.parametrize(
    "source",
    [
        "@dataclass\nclass Deps:\n    repository: ActivityRepository\n",
        (
            "@dataclasses.dataclass(frozen=True)\n"
            "class Deps:\n    repository: ActivityRepository | None\n"
        ),
        '@dataclass(slots=True)\nclass Deps:\n    repository: "ActivityRepository"\n',
        "class Deps(NamedTuple):\n    clock: Clock\n",
        "class Deps(typing.NamedTuple):\n    clock: mosemo.shared.application.clock.Clock\n",
        "@dataclass\nclass Deps:\n    services: list[LabelService]\n",
        "@dataclass\nclass Deps:\n    factory: async_sessionmaker[AsyncSession]\n",
        "@dataclass\nclass Deps:\n    session: Optional[Session] = None\n",
    ],
)
def test_dependency_bundles_are_rejected(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_dependency_bundles(ast.parse(source))
    assert errors
    assert all("CS-P27" in error for error in errors)


@pytest.mark.parametrize(
    "source",
    [
        "@dataclass(frozen=True)\nclass Event:\n    started_at: datetime\n    n: int\n",
        "@dataclass\nclass Result:\n    session: FocusSession\n",
        "@dataclass\nclass Result:\n    sessions: list[FocusSession] | None\n",
        "class AppState(TypedDict):\n    repository: ActivityRepository\n",
        "class Service:\n    repository: ActivityRepository\n",
    ],
)
def test_value_objects_and_typed_dicts_are_allowed(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_dependency_bundles(ast.parse(source)) == []


PACKAGE = ["mosemo", "x"]


@pytest.mark.parametrize(
    "source",
    [
        "from mosemo.x.schemas import A\n",
        "import mosemo.x.schemas\n",
        "from mosemo.x import schemas\n",
        "from .schemas import A\n",
        "from . import schemas\n",
        "from ..y.schemas import A\n",
        "def f():\n    from mosemo.y import schemas\n",
    ],
)
def test_schema_imports_are_rejected(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_schema_imports(ast.parse(source), PACKAGE)
    assert len(errors) == 1
    assert "CS-P28" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "from mosemo.x.models import A\n",
        "from .mappers import AMapper\n",
        "from mosemo.x import models, schemata\n",
        "import pydantic\n",
    ],
)
def test_other_imports_are_allowed_in_repository(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_schema_imports(ast.parse(source), PACKAGE) == []


@pytest.mark.parametrize(
    "source",
    [
        "async def to_entity(request):\n    pass\n",
        "class AMapper:\n    async def to_entity(self, request):\n        pass\n",
        "from sqlalchemy import select\n",
        "import sqlalchemy.orm\n",
        "from mosemo.x.repository import ARepository\n",
        "from .device_repository import DeviceRepository\n",
        "from mosemo.x import repository\n",
        "from mosemo.infra.database import get_session\n",
        "from mosemo.infra.persistence import Base\n",
        "from mosemo.infra import clock\n",
        "from mosemo.shared.application.clock import Clock\n",
    ],
)
def test_impure_mappers_are_rejected(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_mapper_purity(ast.parse(source), PACKAGE)
    assert len(errors) == 1
    assert "CS-P29" in errors[0]


def test_pure_mapper_is_allowed(conventions_validator: ModuleType) -> None:
    source = (
        "from mosemo.x.schemas import ACreate\n"
        "from .models import A\n"
        "class AMapper:\n"
        "    def to_entity(self, request: ACreate) -> A:\n"
        "        return A(name=request.name)\n"
    )
    assert conventions_validator.check_mapper_purity(ast.parse(source), PACKAGE) == []


@pytest.mark.parametrize(
    "source",
    [
        "class A:\n    def m(self, a, b, c, d, e, f):\n        pass\n",
        "def f(a, b, c, /, d, *, e, f):\n    pass\n",
        "class A:\n    @staticmethod\n    def m(a, b, c, d, e, f):\n        pass\n",
        "class A:\n    @classmethod\n    async def m(cls, a, b, c, d, e, *, f):\n        pass\n",
        "class A:\n    def m(self):\n        def inner(a, b, c, d, e, f):\n            pass\n",
    ],
)
def test_model_functions_over_five_arguments_are_rejected(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_model_argument_counts(ast.parse(source))
    assert len(errors) == 1
    assert "CS-P30" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "class A:\n    def __init__(self, a, b, c, d, e, f, g, h, i):\n        pass\n",
        "class A:\n    def m(self, a, b, c, d, e, *args, **kwargs):\n        pass\n",
        "def f(a, b, c, d, e, *args, **kwargs):\n    pass\n",
        "class A:\n    if X:\n        def m(self, a, b, c, d, e):\n            pass\n",
        (
            "from typing import override\n"
            "class A(B):\n    @override\n    def m(self, a, b, c, d, e, f):\n"
            "        pass\n"
        ),
    ],
)
def test_model_functions_within_limit_are_allowed(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_model_argument_counts(ast.parse(source)) == []


def constructor(decorator: str, name: str = "__init__") -> str:
    return f"class A:\n    {decorator}\n    def {name}(self):\n        pass\n"


@pytest.mark.parametrize(
    "source",
    [
        "from typing import override\n" + constructor("@override"),
        "import typing\n" + constructor("@typing.override"),
        "import typing_extensions\n" + constructor("@typing_extensions.override"),
        "from typing_extensions import override\n" + constructor("@override"),
        "from typing import override as ov\n" + constructor("@ov"),
        "import typing as t\n" + constructor("@t.override"),
        "import typing_extensions as te\n" + constructor("@te.override"),
        "from typing import override\n" + constructor("@override", "__new__"),
    ],
)
def test_constructor_override_is_rejected(
    conventions_validator: ModuleType, source: str
) -> None:
    errors = conventions_validator.check_constructor_override(ast.parse(source))
    assert len(errors) == 1
    assert "CS-P31" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "from typing import override\n" + constructor("@override", "handle"),
        "from typing import final\n" + constructor("@final"),
        "import other as t\n" + constructor("@t.override"),
        "from other import override as ov\n" + constructor("@ov"),
    ],
)
def test_override_outside_constructor_is_allowed(
    conventions_validator: ModuleType, source: str
) -> None:
    assert conventions_validator.check_constructor_override(ast.parse(source)) == []


def test_validate_scopes_role_rules(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(ALLOWED_PYPROJECT)
    bundle = "@dataclass\nclass Deps:\n    repository: ActivityRepository\n"
    schema_import = "from .schemas import A\n"
    long_method = "class A:\n    def m(self, a, b, c, d, e, f):\n        pass\n"
    init_override = "from typing import override\n" + constructor("@override")
    files = {
        "src/mosemo/x/service.py": bundle + schema_import + long_method,
        "src/mosemo/x/repository.py": schema_import,
        "src/mosemo/x/device_repository.py": schema_import,
        "src/mosemo/x/models.py": schema_import + long_method,
        "src/mosemo/x/mappers.py": schema_import + "import sqlalchemy\n",
        "tests/test_x.py": bundle + init_override,
        "scripts/x.py": init_override,
        "migrations/x.py": init_override,
        ".codex/x.py": init_override,
    }
    for name, source in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(source)
    errors = rule_errors(
        conventions_validator.validate(tmp_path),
        "CS-P27",
        "CS-P28",
        "CS-P29",
        "CS-P30",
        "CS-P31",
    )
    assert sorted(error.split(": ", 2)[:2] for error in errors) == [
        [".codex/x.py:4", "CS-P31"],
        ["migrations/x.py:4", "CS-P31"],
        ["scripts/x.py:4", "CS-P31"],
        ["src/mosemo/x/device_repository.py:1", "CS-P28"],
        ["src/mosemo/x/mappers.py:2", "CS-P29"],
        ["src/mosemo/x/models.py:1", "CS-P28"],
        ["src/mosemo/x/models.py:3", "CS-P30"],
        ["src/mosemo/x/repository.py:1", "CS-P28"],
        ["src/mosemo/x/service.py:3", "CS-P27"],
        ["tests/test_x.py:7", "CS-P31"],
    ]


@pytest.mark.parametrize(
    ("path", "source", "line"),
    [
        ("src/mosemo/x/router.py", "from fastapi import HTTPException\n", 1),
        (
            "src/mosemo/x/router.py",
            "from fastapi.exceptions import HTTPException as E\n",
            1,
        ),
        (
            "src/mosemo/x/router.py",
            "from starlette.exceptions import HTTPException\n",
            1,
        ),
        (
            "src/mosemo/x/router.py",
            "import starlette.exceptions as se\nraise se.HTTPException(404)\n",
            2,
        ),
        (
            "src/mosemo/x/router.py",
            "from fastapi import exceptions as fx\nfx.HTTPException(404)\n",
            2,
        ),
        (
            "src/mosemo/x/router.py",
            "import fastapi\nfastapi.exceptions.HTTPException\n",
            2,
        ),
        (
            "src/mosemo/x/router.py",
            "from mosemo.api.exception_handlers import StarletteHTTPException\n",
            1,
        ),
        ("src/mosemo/x/repository.py", "from fastapi import HTTPException\n", 1),
    ],
)
def test_http_exception_usage_is_rejected(
    conventions_validator: ModuleType,
    tmp_path: Path,
    path: str,
    source: str,
    line: int,
) -> None:
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(source)
    errors = rule_errors(conventions_validator.validate(tmp_path), "CS-P12")
    assert [error.split(": ", 2)[:2] for error in errors] == [
        [f"{path}:{line}", "CS-P12"]
    ]


def test_http_exception_usage_is_allowed_in_handlers_and_outside_src(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    usage = "from fastapi import HTTPException\n"
    files = {
        "src/mosemo/api/exception_handlers.py": usage,
        "tests/x.py": usage,
        "src/mosemo/x/router.py": "import fastapi\nfrom fastapi import Depends\n",
    }
    for name, source in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(source)
    assert rule_errors(conventions_validator.validate(tmp_path), "CS-P12") == []

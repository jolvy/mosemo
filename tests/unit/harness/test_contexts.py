"""BC 패키지 AST 규칙(ADR 0011~0013)과 BC 경로의 CS-P13 개정을 fixture BC로 검사한다."""

from pathlib import Path
from types import ModuleType

import pytest

BC = "src/mosemo/sample"
MINIMAL_CONTEXT = {
    f"{BC}/public.py": "from .application.catalog import SampleCatalog\n",
    f"{BC}/application/catalog.py": (
        "class SampleCatalog:\n"
        "    async def is_active(self, db_session, sample_id):\n"
        "        return True\n"
    ),
}
VALID_CONTEXT = {
    f"{BC}/__init__.py": '"""Sample bounded context."""\n',
    f"{BC}/public.py": (
        "from mosemo.sample.application.catalog import SampleCatalog\n"
        "from mosemo.sample.domain.events import SampleCreated\n"
    ),
    f"{BC}/bootstrap.py": "def register_handlers(dispatcher):\n    pass\n",
    f"{BC}/application/ports.py": (
        "from typing import Protocol\n\n"
        "class SampleSuggester(Protocol):\n"
        "    async def suggest(self) -> str: ...\n"
    ),
    f"{BC}/application/catalog.py": (
        "from sqlalchemy.ext.asyncio import AsyncSession\n\n"
        "class SampleCatalog:\n"
        "    async def is_active(self, db_session: AsyncSession) -> bool:\n"
        "        return True\n"
    ),
    f"{BC}/application/samples.py": (
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "from mosemo.infra.transactions import read_snapshot, unit_of_work\n"
        "from mosemo.shared.application.use_case import UseCase\n\n"
        "class StartSample(UseCase):\n"
        "    def __init__(self, *, dispatcher):\n"
        "        self._dispatcher = dispatcher\n\n"
        "    async def execute(self, db_session: AsyncSession) -> None:\n"
        "        async with unit_of_work(db_session, self._dispatcher):\n"
        "            pass\n"
        "        await self._read(db_session)\n\n"
        "    async def _read(self, db_session: AsyncSession) -> None:\n"
        "        async def inner():\n"
        "            async with read_snapshot(db_session):\n"
        "                pass\n"
        "        await inner()\n"
    ),
    f"{BC}/domain/events.py": (
        "from dataclasses import dataclass\n"
        "from mosemo.shared.domain.events import DomainEvent\n\n"
        "@dataclass(frozen=True)\n"
        "class SampleCreated(DomainEvent):\n"
        "    sample_id: int\n\n"
        "@dataclass(frozen=True)\n"
        "class SampleWithdrawn(DomainEvent):\n"
        "    sample_id: int\n"
    ),
    f"{BC}/domain/samples.py": (
        "from mosemo.shared.domain.aggregates import AggregateRoot\n"
        "from .events import SampleCreated\n\n"
        "class Sample(AggregateRoot):\n"
        "    def mark_created(self) -> None:\n"
        '        """Record creation."""\n'
        "        self.events.append(SampleCreated(sample_id=self.id))\n"
    ),
    f"{BC}/infrastructure/repository.py": (
        "from sqlalchemy import insert, select\n"
        "from mosemo.sample.domain.samples import Sample\n"
        "from .tables import samples\n\n"
        "class SampleRepository:\n"
        "    async def get_or_create(self, db_session, sample: Sample):\n"
        "        statement = insert(Sample).values(id=sample.id).returning(Sample)\n"
        "        rows = select(Sample).where(samples.c.id == Status.ACTIVE)\n"
        "        return Sample.create(statement, rows), Sample.__name__\n"
    ),
    f"{BC}/infrastructure/queries.py": (
        "from sqlalchemy import select\n\n"
        "class SampleQueries:\n"
        "    async def list_names(self, db_session):\n"
        "        return await db_session.scalars(select(1))\n"
    ),
    f"{BC}/presentation/router.py": (
        "from fastapi import APIRouter\n\n"
        "def create_router(*, start):\n"
        "    router = APIRouter()\n\n"
        '    @router.post("/samples")\n'
        "    async def start_sample(db_session):\n"
        "        result = await start.execute(db_session)\n"
        "        return Response.model_validate(result)\n\n"
        "    return router\n"
    ),
}


def write_files(root: Path, files: dict[str, str]) -> None:
    files = {"src/mosemo/api/main.py": "", **files}
    for name, source in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(source)


def errors_for(
    validator: ModuleType, root: Path, files: dict[str, str], marker: str = "ADR 00"
) -> list[str]:
    write_files(root, files)
    return [error for error in validator.validate(root) if marker in error]


def locations(errors: list[str]) -> list[str]:
    return sorted(error.split(": ", 1)[0] for error in errors)


def test_minimal_context_with_public_and_application_only_passes(
    architecture_validator: ModuleType,
    conventions_validator: ModuleType,
    tmp_path: Path,
) -> None:
    write_files(tmp_path, MINIMAL_CONTEXT)

    assert architecture_validator.validate(tmp_path) == []
    assert [
        error
        for error in conventions_validator.validate(tmp_path)
        if error.startswith((f"{BC}/", "src/mosemo/old/"))
    ] == []


def test_valid_context_passes_context_rules(
    architecture_validator: ModuleType,
    conventions_validator: ModuleType,
    tmp_path: Path,
) -> None:
    write_files(tmp_path, VALID_CONTEXT)

    assert architecture_validator.validate(tmp_path) == []
    assert [
        error
        for error in conventions_validator.validate(tmp_path)
        if error.startswith((f"{BC}/", "src/mosemo/old/"))
    ] == []


def test_package_without_public_module_is_not_a_context(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    files = {"src/mosemo/old/helpers.py": "class P(Protocol):\n    pass\n"}

    assert errors_for(architecture_validator, tmp_path, files) == []


@pytest.mark.parametrize(
    ("name", "location"),
    [
        ("helpers.py", f"{BC}/helpers.py"),
        ("schemas.py", f"{BC}/schemas.py"),
        ("README.md", f"{BC}/README.md"),
        ("services/x.py", f"{BC}/services"),
    ],
)
def test_context_root_rejects_files_outside_allowed_list(
    architecture_validator: ModuleType, tmp_path: Path, name: str, location: str
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/{name}": ""}

    errors = errors_for(architecture_validator, tmp_path, files, "ADR 0011")

    assert locations(errors) == [location]
    assert "BC 루트에는" in errors[0]


@pytest.mark.parametrize(
    "source",
    [
        "from .application.catalog import SampleCatalog\n",
        '"""Doc."""\n__all__ = []\n',
        "x = 1\n",
    ],
)
def test_context_root_init_rejects_statements_besides_docstring(
    architecture_validator: ModuleType, tmp_path: Path, source: str
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/__init__.py": source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert len(errors) == 1
    assert f"{BC}/__init__.py:" in errors[0]
    assert "docstring만" in errors[0]


def test_layer_init_is_not_restricted(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/application/__init__.py": "x = 1\n"}

    assert errors_for(architecture_validator, tmp_path, files) == []


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("domain/samples.py", "class P(Protocol):\n    pass\n"),
        ("application/catalog.py", "class P(typing.Protocol):\n    pass\n"),
        ("infrastructure/repository.py", "class P(Protocol[T]):\n    pass\n"),
        ("application/sub/ports.py", "class P(Protocol):\n    pass\n"),
    ],
)
def test_protocol_outside_application_ports_is_rejected(
    architecture_validator: ModuleType, tmp_path: Path, path: str, source: str
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/{path}": source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert locations(errors) == [f"{BC}/{path}:1"]
    assert "`application/ports.py`에만" in errors[0]


USE_CASE_IMPORT = "from mosemo.shared.application.use_case import UseCase\n"


@pytest.mark.parametrize(
    ("path", "source", "message"),
    [
        (
            "domain/samples.py",
            "class Start(UseCase):\n    async def execute(self):\n        pass\n",
            "`application`에만",
        ),
        (
            "presentation/router.py",
            "class Start(UseCase):\n    async def execute(self):\n        pass\n",
            "`application`에만",
        ),
        (
            "application/samples.py",
            "class Start(UseCase):\n    def execute(self):\n        pass\n",
            "`async def execute` 하나만",
        ),
        (
            "application/samples.py",
            (
                "class Start(UseCase):\n"
                "    async def execute(self):\n        pass\n"
                "    async def preview(self):\n        pass\n"
            ),
            "`async def execute` 하나만",
        ),
        (
            "application/samples.py",
            "class Start(UseCase):\n    async def run(self):\n        pass\n",
            "`async def execute` 하나만",
        ),
        (
            "application/samples.py",
            (
                "class Base(UseCase):\n    async def execute(self):\n        pass\n"
                "class Start(Base):\n    async def other(self):\n        pass\n"
            ),
            "`async def execute` 하나만",
        ),
    ],
)
def test_use_case_location_and_public_methods_are_checked(
    architecture_validator: ModuleType,
    tmp_path: Path,
    path: str,
    source: str,
    message: str,
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/{path}": USE_CASE_IMPORT + source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert len(errors) == 1
    assert message in errors[0]


def test_application_class_with_execute_must_inherit_use_case(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    source = "class Start:\n    async def execute(self):\n        pass\n"
    files = {**MINIMAL_CONTEXT, f"{BC}/application/samples.py": source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert locations(errors) == [f"{BC}/application/samples.py:1"]
    assert "`UseCase`를 상속한다" in errors[0]


@pytest.mark.parametrize(
    ("path", "source", "line"),
    [
        (
            "application/samples.py",
            (
                "async def helper(db_session, dispatcher):\n"
                "    async with unit_of_work(db_session, dispatcher):\n"
                "        pass\n"
            ),
            2,
        ),
        (
            "application/samples.py",
            (
                "class Helper:\n"
                "    async def run(self, db_session):\n"
                "        async with transactions.read_snapshot(db_session):\n"
                "            pass\n"
            ),
            3,
        ),
        (
            "application/samples.py",
            USE_CASE_IMPORT + "class Start(UseCase):\n"
            "    helper = unit_of_work\n"
            "    async def execute(self):\n        pass\n",
            3,
        ),
        ("infrastructure/repository.py", "run = read_snapshot\n", 1),
        ("presentation/router.py", "uow = unit_of_work(s, d)\n", 1),
    ],
)
def test_transaction_helpers_outside_use_case_methods_are_rejected(
    architecture_validator: ModuleType,
    tmp_path: Path,
    path: str,
    source: str,
    line: int,
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/{path}": source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert locations(errors) == [f"{BC}/{path}:{line}"]
    assert "`UseCase` 하위 클래스의 메서드 안에서만" in errors[0]


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("        return await repository.find(db_session)\n", 5),
        ("        return await db_session.execute(statement)\n", 5),
        ("        return await catalog.is_active(db_session)\n", 5),
        ("        await start.execute(await load())\n", 5),
    ],
)
def test_router_calls_only_use_case_execute(
    architecture_validator: ModuleType, tmp_path: Path, body: str, line: int
) -> None:
    source = (
        "def create_router(*, start):\n"
        "    router = APIRouter()\n"
        '    @router.get("/samples")\n'
        "    async def list_samples(db_session):\n" + body + "    return router\n"
    )
    files = {**MINIMAL_CONTEXT, f"{BC}/presentation/router.py": source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert locations(errors) == [f"{BC}/presentation/router.py:{line}"]
    assert "`execute`만" in errors[0]


def test_router_rule_ignores_non_route_presentation_functions(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    source = (
        "async def get_token(request):\n    return await request.body()\n"
        "@cache\nasync def provider():\n    return await build()\n"
    )
    files = {**MINIMAL_CONTEXT, f"{BC}/presentation/providers.py": source}

    assert errors_for(architecture_validator, tmp_path, files) == []


@pytest.mark.parametrize(
    "rule",
    [
        "UseCase",
        "execute",
        "unit_of_work",
    ],
)
def test_public_collaborator_rules(
    architecture_validator: ModuleType, tmp_path: Path, rule: str
) -> None:
    bodies = {
        "UseCase": "class SampleCatalog(UseCase):\n    async def lock(self):\n"
        "        pass\n",
        "execute": "class SampleCatalog:\n    async def execute(self, db_session):\n"
        "        pass\n",
        "unit_of_work": "class SampleCatalog:\n"
        "    async def lock(self, db_session, dispatcher):\n"
        "        async with unit_of_work(db_session, dispatcher):\n"
        "            pass\n",
    }
    files = {
        **MINIMAL_CONTEXT,
        f"{BC}/application/catalog.py": USE_CASE_IMPORT + bodies[rule],
    }

    errors = errors_for(architecture_validator, tmp_path, files)

    collaborator = [error for error in errors if "공개 협력자 `SampleCatalog`" in error]
    assert len(collaborator) == 1
    assert collaborator[0].startswith(f"{BC}/application/catalog.py:2: ")
    assert f"`{rule}`" in collaborator[0]


@pytest.mark.parametrize(
    "public",
    [
        "from .application.catalog import SampleCatalog as Catalog\n",
        "from mosemo.sample.application.catalog import SampleCatalog\n",
        "from .application import SampleCatalog\n",
    ],
)
def test_public_collaborators_are_resolved_from_public_imports(
    architecture_validator: ModuleType, tmp_path: Path, public: str
) -> None:
    source = "class SampleCatalog:\n    async def execute(self):\n        pass\n"
    files = {
        f"{BC}/public.py": public,
        f"{BC}/application/catalog.py": source,
        f"{BC}/application/__init__.py": source,
    }

    errors = errors_for(architecture_validator, tmp_path, files)

    assert any("공개 협력자 `SampleCatalog`" in error for error in errors)


def test_non_application_public_exports_are_not_collaborators(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    files = {
        f"{BC}/public.py": "from .domain.values import Thing\n",
        f"{BC}/domain/values.py": "class Thing:\n    def execute(self):\n"
        "        pass\n",
    }

    assert errors_for(architecture_validator, tmp_path, files) == []


def test_session_factories_and_callables_may_mention_async_session(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    source = (
        "def build(*, session_factory: async_sessionmaker[AsyncSession]):\n"
        "    pass\n"
        "def run(*, opener: Callable[[AsyncSession], None]):\n    pass\n"
    )
    files = {**MINIMAL_CONTEXT, f"{BC}/bootstrap.py": source}

    assert errors_for(architecture_validator, tmp_path, files) == []


def test_context_root_ignores_dotfiles(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/.DS_Store": ""}

    assert errors_for(architecture_validator, tmp_path, files) == []


def test_mark_rule_is_limited_to_domain(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    source = "class SampleRepository:\n    def mark_processed(self, ids):\n"
    source += "        self.ids = ids\n"
    files = {**MINIMAL_CONTEXT, f"{BC}/infrastructure/repository.py": source}

    assert errors_for(architecture_validator, tmp_path, files) == []


def test_public_collaborators_are_followed_through_re_exports(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    files = {
        f"{BC}/public.py": "from .application import SampleCatalog\n",
        f"{BC}/application/__init__.py": "from .catalog import SampleCatalog\n",
        f"{BC}/application/catalog.py": USE_CASE_IMPORT
        + "class SampleCatalog(UseCase):\n    async def lock(self):\n        pass\n",
    }

    errors = errors_for(architecture_validator, tmp_path, files)

    assert any(
        error.startswith(f"{BC}/application/catalog.py:2: ")
        and "공개 협력자 `SampleCatalog`는 `UseCase`" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("source", "line"),
    [
        ("async def f(session: AsyncSession):\n    pass\n", 1),
        ("async def f(session: Optional[AsyncSession]):\n    pass\n", 1),
        ("def f(*, s: 'AsyncSession'):\n    pass\n", 1),
        ("async def f(session: AsyncSession | None = None):\n    pass\n", 1),
        (
            "class R:\n    async def find(self, sess: sa.AsyncSession):\n        pass\n",
            2,
        ),
    ],
)
def test_async_session_parameter_must_be_named_db_session(
    architecture_validator: ModuleType, tmp_path: Path, source: str, line: int
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/infrastructure/repository.py": source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert locations(errors) == [f"{BC}/infrastructure/repository.py:{line}"]
    assert "`db_session`으로 바꾼다" in errors[0]


@pytest.mark.parametrize(
    "init",
    [
        "def __init__(self, db_session):",
        "def __init__(self, *, session_factory: async_sessionmaker):",
        "def __init__(self, session):",
        "def __init__(self, *, s: 'AsyncSession'):",
    ],
)
@pytest.mark.parametrize("name", ["SampleRepository", "SampleQuery", "SampleQueries"])
def test_repository_and_query_constructors_reject_sessions(
    architecture_validator: ModuleType, tmp_path: Path, init: str, name: str
) -> None:
    source = f"class {name}:\n    {init}\n        pass\n"
    files = {**MINIMAL_CONTEXT, f"{BC}/infrastructure/samples.py": source}

    errors = [
        error
        for error in errors_for(architecture_validator, tmp_path, files)
        if "생성자에서 세션" in error
    ]

    assert locations(errors) == [f"{BC}/infrastructure/samples.py:2"]


def test_other_class_constructors_may_take_dependencies(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    source = (
        "class Builder:\n    def __init__(self, *, session_factory):\n        pass\n"
    )
    files = {**MINIMAL_CONTEXT, f"{BC}/infrastructure/builder.py": source}

    assert errors_for(architecture_validator, tmp_path, files) == []


@pytest.mark.parametrize(
    "file", ["queries.py", "query.py", "sample_queries.py", "reads/label_query.py"]
)
@pytest.mark.parametrize(
    ("source", "name"),
    [
        ("from sqlalchemy import insert\n", "insert"),
        ("statement = update(samples)\n", "update"),
        ("statement = samples.delete()\n", "delete"),
        ("db_session.add(sample)\n", "add"),
        ("db_session.add_all(samples)\n", "add_all"),
    ],
)
def test_query_files_reject_writes(
    architecture_validator: ModuleType,
    tmp_path: Path,
    file: str,
    source: str,
    name: str,
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/infrastructure/{file}": source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert locations(errors) == [f"{BC}/infrastructure/{file}:1"]
    assert f"`{name}`" in errors[0]


def test_query_write_rule_is_limited_to_infrastructure_query_files(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    source = "from sqlalchemy import insert\nsamples.delete()\n"
    files = {
        **MINIMAL_CONTEXT,
        f"{BC}/infrastructure/repository.py": source,
        f"{BC}/application/queries.py": source,
    }

    assert errors_for(architecture_validator, tmp_path, files) == []


EVENT_IMPORT = (
    "from dataclasses import dataclass\n"
    "from mosemo.shared.domain.events import DomainEvent\n"
)


@pytest.mark.parametrize(
    ("path", "source", "message"),
    [
        (
            "domain/samples.py",
            "@dataclass(frozen=True)\nclass SampleCreated(DomainEvent):\n    pass\n",
            "`domain/events.py`에만",
        ),
        (
            "application/samples.py",
            "@dataclass(frozen=True)\nclass SampleCreated(DomainEvent):\n    pass\n",
            "`domain/events.py`에만",
        ),
        (
            "domain/events.py",
            "@dataclass\nclass SampleCreated(DomainEvent):\n    pass\n",
            "`@dataclass(frozen=True)`",
        ),
        (
            "domain/events.py",
            "@dataclass(frozen=False)\nclass SampleCreated(DomainEvent):\n    pass\n",
            "`@dataclass(frozen=True)`",
        ),
        (
            "domain/events.py",
            "class SampleCreated(DomainEvent):\n    pass\n",
            "`@dataclass(frozen=True)`",
        ),
        (
            "domain/events.py",
            (
                "@dataclass(frozen=True)\nclass SampleCreatedEvent(DomainEvent):\n"
                "    pass\n"
            ),
            "`Event` 접미사",
        ),
        (
            "domain/events.py",
            "@dataclass(frozen=True)\nclass SampleCreate(DomainEvent):\n    pass\n",
            "과거분사",
        ),
        (
            "domain/events.py",
            "@dataclass(frozen=True)\nclass SampleGrow(DomainEvent):\n    pass\n",
            "과거분사",
        ),
        (
            "domain/events.py",
            "@dataclass(frozen=True)\nclass Payload:\n    pass\n",
            "`DomainEvent`를 상속한다",
        ),
    ],
)
def test_event_location_base_frozen_and_name(
    architecture_validator: ModuleType,
    tmp_path: Path,
    path: str,
    source: str,
    message: str,
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/{path}": EVENT_IMPORT + source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert len(errors) == 1
    assert message in errors[0]


@pytest.mark.parametrize(
    "name", ["SampleCreated", "LabelConfirmed", "SessionWithdrawn", "TimelineReset"]
)
def test_event_names_allow_ed_and_irregular_past_participles(
    architecture_validator: ModuleType, tmp_path: Path, name: str
) -> None:
    source = f"@dataclass(frozen=True)\nclass {name}(DomainEvent):\n    pass\n"
    files = {**MINIMAL_CONTEXT, f"{BC}/domain/events.py": EVENT_IMPORT + source}

    assert errors_for(architecture_validator, tmp_path, files) == []


def test_events_inheriting_an_event_base_are_checked(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    source = (
        EVENT_IMPORT + "@dataclass(frozen=True)\nclass SampleChanged(DomainEvent):\n"
        "    pass\n"
    )
    files = {
        **MINIMAL_CONTEXT,
        f"{BC}/domain/events.py": source,
        f"{BC}/domain/samples.py": "class SampleMoved(SampleChanged):\n    pass\n",
    }

    errors = errors_for(architecture_validator, tmp_path, files)

    assert locations(errors) == [
        f"{BC}/domain/samples.py:1",
        f"{BC}/domain/samples.py:1",
    ]


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("        self.created = True\n", 3),
        ("        self.events.append(Created())\n        self.count += 1\n", 4),
        ("        self.events.extend([Created()])\n", 3),
        ("        if self.new:\n            self.events.append(Created())\n", 3),
        ("        events.append(Created())\n", 3),
    ],
)
def test_mark_methods_only_append_events(
    architecture_validator: ModuleType, tmp_path: Path, body: str, line: int
) -> None:
    source = "class Sample:\n    def mark_created(self):\n" + body
    files = {**MINIMAL_CONTEXT, f"{BC}/domain/samples.py": source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert locations(errors) == [f"{BC}/domain/samples.py:{line}"]
    assert "`self.events.append(...)`" in errors[0]


@pytest.mark.parametrize(
    ("source", "line"),
    [
        (
            (
                "from mosemo.sample.domain.samples import Sample\n"
                "statement = select(samples).where(Sample.account_id == 1)\n"
            ),
            2,
        ),
        (
            (
                "from ..domain.samples import Sample as S\n"
                "statement = select(samples).order_by(S.started_at)\n"
            ),
            2,
        ),
    ],
)
def test_infrastructure_sql_rejects_domain_class_attributes(
    architecture_validator: ModuleType, tmp_path: Path, source: str, line: int
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/infrastructure/repository.py": source}

    errors = errors_for(architecture_validator, tmp_path, files)

    assert locations(errors) == [f"{BC}/infrastructure/repository.py:{line}"]
    assert "`Table` 컬럼" in errors[0]


def test_domain_attribute_rule_is_limited_to_infrastructure(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    source = (
        "from mosemo.sample.domain.samples import Sample\nvalue = Sample.account_id\n"
    )
    files = {**MINIMAL_CONTEXT, f"{BC}/application/samples.py": source}

    assert errors_for(architecture_validator, tmp_path, files) == []


def test_context_errors_report_syntax_failures(
    architecture_validator: ModuleType, tmp_path: Path
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/domain/broken.py": "def (:\n"}
    write_files(tmp_path, files)

    errors = architecture_validator.validate(tmp_path)

    assert [error.split(": ", 1)[0] for error in errors] == [f"{BC}/domain/broken.py"]


def test_old_role_rules_stay_on_old_paths(
    architecture_validator: ModuleType,
    conventions_validator: ModuleType,
    tmp_path: Path,
) -> None:
    old_import = "from .service import S\nfrom sqlalchemy import select\n"
    repository = "class R:\n    async def get_or_create(self):\n        pass\n"
    files = {
        **MINIMAL_CONTEXT,
        f"{BC}/infrastructure/repository.py": old_import + repository,
        f"{BC}/infrastructure/models.py": "from .schemas import Request\n",
        f"{BC}/infrastructure/tables.py": "from sqlalchemy import select\n",
        "src/mosemo/old/repository.py": old_import + repository,
    }
    write_files(tmp_path, files)

    architecture = architecture_validator.validate(tmp_path)
    conventions = [
        error
        for error in conventions_validator.validate(tmp_path)
        if error.startswith((f"{BC}/", "src/mosemo/old/"))
    ]

    assert locations(architecture) == ["src/mosemo/old/repository.py:1"]
    assert "CS-P32" in architecture[0]
    assert locations(conventions) == ["src/mosemo/old/repository.py:4"]
    assert "CS-P10" in conventions[0]


@pytest.mark.parametrize(
    "file",
    [
        "application/processing.py",
        "application/service.py",
        "infrastructure/executor.py",
        "infrastructure/transactions.py",
        "presentation/router.py",
    ],
)
@pytest.mark.parametrize(
    "call", ["db_session.begin()", "db_session.commit()", "self._s.rollback()"]
)
def test_context_transaction_calls_are_rejected_regardless_of_file_name(
    conventions_validator: ModuleType, tmp_path: Path, file: str, call: str
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/{file}": f"{call}\n"}
    write_files(tmp_path, files)

    errors = [
        error for error in conventions_validator.validate(tmp_path) if "CS-P13" in error
    ]

    assert locations(errors) == [f"{BC}/{file}:1"]
    assert "`infra.transactions`" in errors[0]


def test_old_paths_keep_cs_p13_file_name_allowance(
    conventions_validator: ModuleType, tmp_path: Path
) -> None:
    files = {
        **MINIMAL_CONTEXT,
        "src/mosemo/old/service.py": "session.begin()\n",
        "src/mosemo/old/repository.py": "session.commit()\n",
    }
    write_files(tmp_path, files)

    errors = [
        error for error in conventions_validator.validate(tmp_path) if "CS-P13" in error
    ]

    assert locations(errors) == ["src/mosemo/old/repository.py:1"]


@pytest.mark.parametrize(
    "file", ["presentation/router.py", "presentation/errors.py", "application/x.py"]
)
def test_http_exception_is_rejected_in_context_packages(
    conventions_validator: ModuleType, tmp_path: Path, file: str
) -> None:
    files = {**MINIMAL_CONTEXT, f"{BC}/{file}": "from fastapi import HTTPException\n"}
    write_files(tmp_path, files)

    errors = [
        error for error in conventions_validator.validate(tmp_path) if "CS-P12" in error
    ]

    assert locations(errors) == [f"{BC}/{file}:1"]


def test_real_repository_satisfies_context_rules(
    architecture_validator: ModuleType,
) -> None:
    assert [
        error
        for context in architecture_validator.context_dirs(architecture_validator.ROOT)
        for error in architecture_validator.validate_context(
            architecture_validator.ROOT, context
        )
    ] == []

"""BC 패키지의 매핑·Repository·`public.py`·라우터를 실행 시점 객체로 검사한다(ADR 0011·0013).

BC 패키지는 `src/mosemo/{bc}/public.py`가 있는 패키지이고 있는 것만 검사한다. 규칙마다
fixture 객체로 위반 사례를 확인한다.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from inspect import isfunction
from pathlib import Path
from pkgutil import walk_packages
from types import ModuleType
from typing import Any, get_args, get_type_hints

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table
from sqlalchemy.orm import Mapper, registry, relationship

import mosemo
from mosemo.infra.persistence import mapper_registry
from mosemo.shared.domain.aggregates import AggregateRoot
from mosemo.shared.presentation.current_account import (
    CurrentAccountDep,
    get_current_account,
)

SOURCE = Path(mosemo.__file__).parent
# ADR 0013: 식별자 이름 규칙의 예외.
COMPOSITE_PK_TABLES = {"activity_batch_receipts"}
NATURAL_PRIMARY_KEYS = {
    ("activity_records", "event_id"),  # 클라이언트가 만든 식별자
    ("native_auth_codes", "code_digest"),  # 인가 코드 해시인 자연 키
}
ROLE_FOREIGN_KEYS = {"first_event_id"}  # 역할이 붙은 참조
# ADR 0011: 현재 계정 provider를 받지 않는 공개 엔드포인트(로그인 전 호출).
PUBLIC_ENDPOINTS = {
    ("GET", "/api/v1/auth/kakao/login"),
    ("GET", "/api/v1/auth/kakao/callback"),
    ("POST", "/api/v1/auth/token"),
}
# 선언만 있고 조립 루트가 `dependency_overrides`로 구현을 넣어야 하는 provider.
REQUIRED_OVERRIDES = (get_current_account,)


def context_names(source: Path = SOURCE) -> set[str]:
    return {path.parent.name for path in source.glob("*/public.py")}


def bootstrapped_context_names(source: Path = SOURCE) -> set[str]:
    return {path.parent.name for path in source.glob("*/bootstrap.py")} & (
        context_names(source)
    )


def context_of(module: str, contexts: set[str]) -> str | None:
    parts = module.split(".")
    if len(parts) > 1 and parts[0] == "mosemo" and parts[1] in contexts:
        return parts[1]
    return None


def context_modules(contexts: set[str]) -> list[ModuleType]:
    modules = []
    for context in sorted(contexts):
        package = import_module(f"mosemo.{context}")
        modules.append(package)
        modules.extend(
            import_module(info.name)
            for info in walk_packages(package.__path__, f"{package.__name__}.")
        )
    return modules


def qualified(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def check_dataclass_declarations(classes: Iterable[type]) -> list[str]:
    """매핑 클래스가 `@dataclass(eq=False, repr=False, kw_only=True)`이고 slots가 없는지."""
    errors = []
    for cls in classes:
        params = cls.__dict__.get("__dataclass_params__")
        if (
            params is None
            or params.eq
            or params.repr
            or not params.kw_only
            or params.slots
            or "__slots__" in cls.__dict__
        ):
            errors.append(
                f"{qualified(cls)}: ADR 0013: 매핑 클래스는 "
                "`@dataclass(eq=False, repr=False, kw_only=True)`로 직접 선언하고 "
                "slots를 쓰지 않는다"
            )
    return errors


def singular(table: str) -> str:
    if table.endswith("ies"):
        return table[:-3] + "y"
    if table.endswith(("ches", "shes", "sses", "xes")):
        return table[:-2]
    return table.removesuffix("s")


def check_identifier_columns(tables: Iterable[Table]) -> list[str]:
    """PK 컬럼은 `id`, FK 컬럼은 `{참조 테이블 단수}_id`인지(예외 목록 제외)."""
    errors = []
    for table in tables:
        primary = [column.name for column in table.primary_key.columns]
        natural = len(primary) == 1 and (table.name, primary[0]) in NATURAL_PRIMARY_KEYS
        if table.name not in COMPOSITE_PK_TABLES and not natural and primary != ["id"]:
            errors.append(
                f"{table.name}: ADR 0013: PK 컬럼은 `id` 하나로 한다(현재 {primary})"
            )
        for key in table.foreign_keys:
            column = key.parent.name
            expected = f"{singular(key.target_fullname.split('.')[-2])}_id"
            if column != expected and column not in ROLE_FOREIGN_KEYS:
                errors.append(
                    f"{table.name}.{column}: ADR 0013: FK 컬럼 이름은 "
                    f"`{expected}`로 한다"
                )
    return errors


def check_mapping_owners(mappers: Iterable[Mapper[Any]]) -> list[str]:
    """매핑 클래스가 `AggregateRoot`이거나 root에서 `relationship`으로 닿는 하위인지."""
    mappers = list(mappers)
    stack = [mapper for mapper in mappers if issubclass(mapper.class_, AggregateRoot)]
    owned = {mapper.class_ for mapper in stack}
    while stack:
        for relation in stack.pop().relationships:
            if relation.mapper.class_ not in owned:
                owned.add(relation.mapper.class_)
                stack.append(relation.mapper)
    return [
        f"{qualified(mapper.class_)}: ADR 0013: 매핑 클래스는 `AggregateRoot`이거나 "
        "어느 root의 매핑에서 `relationship`으로 연결된 하위 엔티티다"
        for mapper in mappers
        if mapper.class_ not in owned
    ]


def hinted_types(hint: object) -> set[object]:
    return {hint}.union(*(hinted_types(argument) for argument in get_args(hint)))


def repository_entities(repository: type, entities: set[type]) -> set[type]:
    """Repository 공개 메서드의 인자·반환 타입에 나오는 매핑 클래스를 모은다."""
    found: set[type] = set()
    for name, method in vars(repository).items():
        if name.startswith("_") or not isfunction(method):
            continue
        for hint in get_type_hints(method).values():
            found |= {
                cls
                for cls in hinted_types(hint)
                if isinstance(cls, type) and cls in entities
            }
    return found


def check_repositories(
    repositories: Iterable[type], *, roots: set[type], entities: set[type]
) -> list[str]:
    """Repository마다 root 하나만 다루고, root마다 Repository가 하나인지."""
    errors = []
    owners: dict[type, list[type]] = defaultdict(list)
    for repository in repositories:
        found = repository_entities(repository, entities)
        errors.extend(
            f"{qualified(repository)}: ADR 0013: Repository의 인자·반환 타입에 하위 "
            f"엔티티 `{cls.__name__}`를 쓰지 않는다. root만 주고받는다"
            for cls in sorted(found - roots, key=qualified)
        )
        handled = found & roots
        if len(handled) != 1:
            errors.append(
                f"{qualified(repository)}: ADR 0013: Repository는 aggregate root "
                f"하나만 다룬다(현재 {sorted(cls.__name__ for cls in handled)})"
            )
        for root in handled:
            owners[root].append(repository)
    errors.extend(
        f"{qualified(root)}: ADR 0013: `AggregateRoot`마다 Repository를 하나 둔다"
        f"(현재 {len(owners[root])}개)"
        for root in sorted(roots, key=qualified)
        if len(owners[root]) != 1
    )
    return errors


def check_public_exports(module: ModuleType, entities: set[type]) -> list[str]:
    """`public.py`가 매핑 엔티티나 `AggregateRoot` 하위 클래스를 내보내지 않는지."""
    names = getattr(module, "__all__", None) or [
        name for name in vars(module) if not name.startswith("_")
    ]
    return [
        f"{module.__name__}.{name}: ADR 0013: `public.py`는 매핑 엔티티를 내보내지 "
        "않는다. 다른 BC에는 읽기 모델이나 ID를 준다"
        for name in names
        if isinstance(value := getattr(module, name), type)
        and (value in entities or issubclass(value, AggregateRoot))
    ]


def dependency_calls(dependant: Dependant) -> set[object]:
    return {dependant.call}.union(
        *(dependency_calls(child) for child in dependant.dependencies)
    )


def check_router_providers(app: FastAPI, contexts: set[str]) -> list[str]:
    """BC 라우트가 현재 계정 provider를 받고, 선언만 있는 provider가 override됐는지."""
    errors = []
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or route.endpoint is None:
            continue
        if context_of(route.endpoint.__module__, contexts) is None:
            continue
        calls = dependency_calls(route.dependant)
        endpoints = {(method, route.path) for method in route.methods or ()}
        if get_current_account not in calls and not endpoints <= PUBLIC_ENDPOINTS:
            errors.append(
                f"{sorted(endpoints)}: ADR 0011: BC 라우트는 현재 계정 provider "
                "(`CurrentAccountDep`)를 받는다. 로그인 전 엔드포인트라면 공개 "
                "엔드포인트 예외 목록에 추가하며 리뷰 대상이다"
            )
        errors.extend(
            f"{sorted(endpoints)}: ADR 0011: 조립 루트가 `{provider.__name__}`를 "
            "`dependency_overrides`로 구현하지 않았다"
            for provider in REQUIRED_OVERRIDES
            if provider in calls and provider not in app.dependency_overrides
        )
    return errors


@dataclass(frozen=True)
class Assembly:
    contexts: set[str]
    modules: list[ModuleType]
    mappers: list[Mapper[Any]]

    @property
    def entities(self) -> set[type]:
        return {mapper.class_ for mapper in self.mappers}

    @property
    def roots(self) -> set[type]:
        return {cls for cls in self.entities if issubclass(cls, AggregateRoot)}


@pytest.fixture(scope="module")
def assembly() -> Assembly:
    # 조립 루트가 BC의 매핑 함수를 호출한 뒤의 registry를 본다.
    import_module("mosemo.api.main")
    contexts = context_names()
    return Assembly(
        contexts=contexts,
        modules=context_modules(contexts),
        mappers=[
            mapper
            for mapper in mapper_registry.mappers
            if context_of(mapper.class_.__module__, contexts)
        ],
    )


def test_real_mapped_classes_are_declared_as_entities(assembly: Assembly) -> None:
    assert check_dataclass_declarations(assembly.entities) == []


def test_real_context_tables_follow_identifier_names(assembly: Assembly) -> None:
    values = [
        value
        for module in assembly.modules
        if ".infrastructure" in module.__name__
        for value in vars(module).values()
    ] + [mapper.local_table for mapper in assembly.mappers]

    assert check_identifier_columns({v for v in values if isinstance(v, Table)}) == []


def test_real_mapped_classes_have_owners(assembly: Assembly) -> None:
    assert check_mapping_owners(assembly.mappers) == []


def test_real_repositories_handle_one_root(assembly: Assembly) -> None:
    repositories = [
        value
        for module in assembly.modules
        if ".infrastructure" in module.__name__
        for name, value in vars(module).items()
        if isinstance(value, type)
        and name.endswith("Repository")
        and value.__module__ == module.__name__
    ]

    assert (
        check_repositories(
            repositories, roots=assembly.roots, entities=assembly.entities
        )
        == []
    )


def test_real_public_modules_do_not_export_entities(assembly: Assembly) -> None:
    assert [
        error
        for module in assembly.modules
        if module.__name__.endswith(".public")
        for error in check_public_exports(module, assembly.entities)
    ] == []


def test_real_routers_take_current_account_and_overrides() -> None:
    app = import_module("mosemo.api.main").app

    assert check_router_providers(app, bootstrapped_context_names()) == []


def test_context_discovery_uses_public_module(tmp_path: Path) -> None:
    for name in ["sample/public.py", "sample/bootstrap.py", "other/public.py"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    (tmp_path / "old").mkdir()
    (tmp_path / "old/bootstrap.py").write_text("")

    assert context_names(tmp_path) == {"sample", "other"}
    assert bootstrapped_context_names(tmp_path) == {"sample"}
    assert context_of("mosemo.sample.domain.x", {"sample"}) == "sample"
    assert context_of("mosemo.old.models", {"sample"}) is None


@dataclass(eq=False, repr=False, kw_only=True)
class Owner(AggregateRoot):
    id: int = 0


@dataclass(eq=False, repr=False, kw_only=True)
class Item:
    id: int = 0


@dataclass(eq=False, repr=False, kw_only=True)
class Part:
    id: int = 0


@dataclass(eq=False, repr=False, kw_only=True)
class Orphan:
    id: int = 0


@dataclass(eq=False, repr=False, kw_only=True)
class Other(AggregateRoot):
    id: int = 0


fixture_metadata = MetaData()
fixture_registry = registry(metadata=fixture_metadata)


def fixture_table(name: str, *columns: Column[Any]) -> Table:
    return Table(
        name, fixture_metadata, Column("id", Integer, primary_key=True), *columns
    )


fixture_registry.map_imperatively(
    Owner, fixture_table("owners"), properties={"items": relationship(Item)}
)
fixture_registry.map_imperatively(
    Item,
    fixture_table("items", Column("owner_id", ForeignKey("owners.id"))),
    properties={"parts": relationship(Part)},
)
fixture_registry.map_imperatively(
    Part, fixture_table("parts", Column("item_id", ForeignKey("items.id")))
)
fixture_registry.map_imperatively(Orphan, fixture_table("orphans"))
fixture_registry.map_imperatively(Other, fixture_table("others"))
FIXTURE_ENTITIES: set[type] = {Owner, Item, Part, Orphan, Other}


class Plain:
    pass


@dataclass
class DefaultDataclass:
    pass


@dataclass(eq=False, repr=False)
class PositionalDataclass:
    pass


@dataclass(eq=False, repr=False, kw_only=True, slots=True)
class SlottedDataclass:
    pass


@dataclass(eq=False, repr=False, kw_only=True)
class UndecoratedChild(Owner):
    pass


class InheritedOnly(Owner):
    pass


def test_dataclass_declaration_allows_entity_declaration() -> None:
    assert check_dataclass_declarations([Owner, Item, UndecoratedChild]) == []


@pytest.mark.parametrize(
    "cls",
    [Plain, DefaultDataclass, PositionalDataclass, SlottedDataclass, InheritedOnly],
)
def test_dataclass_declaration_rejects_other_declarations(cls: type) -> None:
    errors = check_dataclass_declarations([cls])

    assert len(errors) == 1
    assert errors[0].startswith(f"{qualified(cls)}: ADR 0013")


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ("accounts", "account"),
        ("activity_batches", "activity_batch"),
        ("categories", "category"),
        ("focus_sessions", "focus_session"),
    ],
)
def test_singular_table_names(table: str, expected: str) -> None:
    assert singular(table) == expected


def identifier_tables() -> MetaData:
    metadata = MetaData()
    Table("accounts", metadata, Column("id", Integer, primary_key=True))
    Table("activity_batches", metadata, Column("id", Integer, primary_key=True))
    Table(
        "activity_records",
        metadata,
        Column("event_id", Integer, primary_key=True),
        Column("account_id", ForeignKey("accounts.id")),
        Column("activity_batch_id", ForeignKey("activity_batches.id")),
    )
    Table(
        "native_auth_codes",
        metadata,
        Column("code_digest", String, primary_key=True),
        Column("account_id", ForeignKey("accounts.id")),
    )
    Table(
        "activity_batch_receipts",
        metadata,
        Column("account_id", ForeignKey("accounts.id"), primary_key=True),
        Column("batch_id", Integer, primary_key=True),
    )
    Table(
        "segments",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("first_event_id", ForeignKey("activity_records.event_id")),
    )
    return metadata


def test_identifier_columns_allow_rule_and_exceptions() -> None:
    assert check_identifier_columns(identifier_tables().tables.values()) == []


@pytest.mark.parametrize(
    ("columns", "location"),
    [
        ([Column("device_id", Integer, primary_key=True)], "devices"),
        ([Column("code_digest", String, primary_key=True)], "devices"),
        (
            [
                Column("id", Integer, primary_key=True),
                Column("batch_id", Integer, primary_key=True),
            ],
            "devices",
        ),
        ([Column("event_id", Integer, primary_key=True)], "devices"),
        (
            [
                Column("id", Integer, primary_key=True),
                Column("owner_id", ForeignKey("accounts.id")),
            ],
            "devices.owner_id",
        ),
        (
            [
                Column("id", Integer, primary_key=True),
                Column("last_event_id", ForeignKey("activity_records.event_id")),
            ],
            "devices.last_event_id",
        ),
    ],
)
def test_identifier_columns_reject_other_names(
    columns: list[Column[Any]], location: str
) -> None:
    metadata = identifier_tables()
    table = Table("devices", metadata, *columns)

    errors = check_identifier_columns([table])

    assert len(errors) == 1
    assert errors[0].startswith(f"{location}: ADR 0013")


def test_mapping_owners_allow_roots_and_transitive_children() -> None:
    mappers = [
        mapper for mapper in fixture_registry.mappers if mapper.class_ is not Orphan
    ]

    assert check_mapping_owners(mappers) == []


def test_mapping_owners_reject_unowned_classes() -> None:
    errors = check_mapping_owners(fixture_registry.mappers)

    assert errors == [
        (
            f"{qualified(Orphan)}: ADR 0013: 매핑 클래스는 `AggregateRoot`이거나 "
            "어느 root의 매핑에서 `relationship`으로 연결된 하위 엔티티다"
        )
    ]


class OwnerRepository:
    async def get_or_create(
        self, db_session: object, owner: Owner
    ) -> tuple[Owner, bool]:
        raise NotImplementedError

    async def find(self, db_session: object, owner_id: int) -> Owner | None:
        raise NotImplementedError

    def _load_item(self, item: Item) -> None:
        raise NotImplementedError


class OtherRepository:
    async def list_for(self, db_session: object) -> list[Other]:
        raise NotImplementedError


class ItemLeakingRepository:
    async def find_item(self, db_session: object) -> Item | None:
        raise NotImplementedError

    async def find(self, db_session: object) -> Other:
        raise NotImplementedError


class TwoRootRepository:
    async def move(self, owner: Owner, other: Other) -> None:
        raise NotImplementedError


class RootlessRepository:
    async def count(self, db_session: object) -> int:
        raise NotImplementedError


ROOTS: set[type] = {Owner, Other}


def test_repositories_allow_one_repository_per_root() -> None:
    errors = check_repositories(
        [OwnerRepository, OtherRepository], roots=ROOTS, entities=FIXTURE_ENTITIES
    )

    assert errors == []


@pytest.mark.parametrize(
    ("repositories", "messages"),
    [
        (
            [OwnerRepository],
            [f"{qualified(Other)}: ADR 0013: `AggregateRoot`마다 Repository를 하나"],
        ),
        (
            [OwnerRepository, OtherRepository, ItemLeakingRepository],
            [
                (
                    f"{qualified(ItemLeakingRepository)}: ADR 0013: Repository의 인자·"
                    "반환 타입에 하위 엔티티 `Item`"
                ),
                f"{qualified(Other)}: ADR 0013: `AggregateRoot`마다 Repository를 하나",
            ],
        ),
        (
            [OwnerRepository, OtherRepository, TwoRootRepository],
            [
                (
                    f"{qualified(TwoRootRepository)}: ADR 0013: Repository는 aggregate "
                    "root 하나만"
                ),
                f"{qualified(Other)}: ADR 0013: `AggregateRoot`마다 Repository를 하나",
                f"{qualified(Owner)}: ADR 0013: `AggregateRoot`마다 Repository를 하나",
            ],
        ),
        (
            [OwnerRepository, OtherRepository, RootlessRepository],
            [
                (
                    f"{qualified(RootlessRepository)}: ADR 0013: Repository는 aggregate "
                    "root 하나만"
                )
            ],
        ),
    ],
)
def test_repositories_reject_child_types_and_root_counts(
    repositories: list[type], messages: list[str]
) -> None:
    errors = check_repositories(repositories, roots=ROOTS, entities=FIXTURE_ENTITIES)

    assert len(errors) == len(messages)
    for error, message in zip(errors, messages, strict=True):
        assert error.startswith(message)


@dataclass(frozen=True)
class OwnerSummary:
    owner_id: int


def public_module(**values: object) -> ModuleType:
    module = ModuleType("mosemo.sample.public")
    for name, value in values.items():
        setattr(module, name, value)
    return module


def test_public_exports_allow_read_models_and_values() -> None:
    module = public_module(OwnerSummary=OwnerSummary, LIMIT=3, _Owner=Owner)

    assert check_public_exports(module, FIXTURE_ENTITIES) == []


@pytest.mark.parametrize(
    "module",
    [
        public_module(Item=Item),
        public_module(Owner=Owner),
        public_module(InheritedOnly=InheritedOnly),
        public_module(__all__=["_Owner"], _Owner=Owner),
    ],
)
def test_public_exports_reject_entities(module: ModuleType) -> None:
    errors = check_public_exports(module, FIXTURE_ENTITIES)

    assert len(errors) == 1
    assert "`public.py`는 매핑 엔티티를 내보내지" in errors[0]


def context_endpoint(path: str, *, current: bool, method: str = "GET") -> APIRouter:
    router = APIRouter()
    if current:

        async def endpoint(account: CurrentAccountDep) -> None:
            pass

    else:

        async def endpoint() -> None:
            pass

    endpoint.__module__ = "mosemo.sample.presentation.router"
    router.add_api_route(path, endpoint, methods=[method])
    return router


def context_app(*routers: APIRouter, overridden: bool = True) -> FastAPI:
    app = FastAPI()
    v1 = APIRouter(prefix="/api/v1")
    for router in routers:
        v1.include_router(router)
    app.include_router(v1)

    async def old_endpoint() -> None:
        pass

    app.add_api_route("/old", old_endpoint)
    if overridden:
        app.dependency_overrides[get_current_account] = old_endpoint
    return app


def test_router_providers_allow_current_account_and_public_endpoints() -> None:
    app = context_app(
        context_endpoint("/samples", current=True),
        context_endpoint("/auth/token", current=False, method="POST"),
        context_endpoint("/auth/kakao/login", current=True),
    )

    assert check_router_providers(app, {"sample"}) == []


def test_router_providers_reject_missing_current_account() -> None:
    app = context_app(
        context_endpoint("/samples", current=False),
        context_endpoint("/auth/token", current=False),
    )

    errors = check_router_providers(app, {"sample"})

    assert [error.split(": ", 1)[0] for error in errors] == [
        "[('GET', '/api/v1/samples')]",
        "[('GET', '/api/v1/auth/token')]",
    ]
    assert all("현재 계정 provider" in error for error in errors)


def test_router_providers_reject_missing_override() -> None:
    app = context_app(context_endpoint("/samples", current=True), overridden=False)

    errors = check_router_providers(app, {"sample"})

    assert errors == [
        (
            "[('GET', '/api/v1/samples')]: ADR 0011: 조립 루트가 `get_current_account`를 "
            "`dependency_overrides`로 구현하지 않았다"
        )
    ]


def test_router_providers_ignore_routes_outside_bootstrapped_contexts() -> None:
    app = context_app(context_endpoint("/samples", current=False), overridden=False)

    assert check_router_providers(app, {"other"}) == []

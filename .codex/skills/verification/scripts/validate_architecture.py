"""선언한 import 경계와 BC 패키지 규칙(ADR 0011~0013)을 AST로 검사한다.

동적 import나 의미적 설계는 판정하지 않는다.
"""

import ast
import re
from dataclasses import dataclass
from importlib.util import resolve_name
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[4]
# ADR 0001·0014: 런타임 오류 응답 모듈은 OpenAPI 모듈을 import하지 않는다.
RUNTIME_ERROR_MODULES = {
    "mosemo.shared.errors",
    "mosemo.shared.presentation.responses",
    "mosemo.api.exception_handlers",
}
OPENAPI_MODULES = (
    "mosemo.shared.openapi",
    "mosemo.shared.error_docs",
    "mosemo.api.openapi",
    "mosemo.api.error_docs",
)
ROLE_RANKS = {
    "router": 5,
    "dependencies": 4,
    "service": 3,
    "mappers": 2,
    "repository": 1,
    "schemas": 1,
    "models": 0,
}
ROLE_DIRECTION_GUIDE = (
    "의존은 router → dependencies → service → mappers → repository·schemas → models"
    " 방향으로만 둔다(방향만 정하며 개별 금지는 CS-P28·CS-P29)."
    " 필요한 값은 인자로 받거나 안쪽 역할로 옮긴다"
)


def imported_modules(tree: ast.AST, package: str) -> list[tuple[int, str]]:
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, name.name) for name in node.names)
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            imports.append((node.lineno, module))
            imports.extend(
                (node.lineno, f"{module}.{name.name}") for name in node.names
            )
    return imports


def role_of(name: str) -> str | None:
    if name.endswith("_repository"):
        return "repository"
    return name if name in ROLE_RANKS else None


def role_direction_error(module: str, target: str) -> str | None:
    if not target.startswith("mosemo."):
        return None
    source_name = module.rsplit(".", 1)[-1]
    target_name = target.rsplit(".", 1)[-1]
    source_role = role_of(source_name)
    target_role = role_of(target_name)
    if source_role is None or target_role is None:
        return None
    if target_role == "schemas" and source_role in {"repository", "models"}:
        return None  # CS-P28이 보고한다.
    if target_role == "router" and source_name in {"models", "repository"}:
        return None  # HTTP 계층 참조 규칙이 보고한다.
    outward = ROLE_RANKS[target_role] > ROLE_RANKS[source_role]
    if not outward and (source_role, target_role) != ("schemas", "repository"):
        return None
    return (
        f"CS-P32: `{source_name}`가 `{target_name}`를 import한다. "
        + ROLE_DIRECTION_GUIDE
    )


def check_source(source: str, module: str) -> list[str]:
    errors = []
    for line, target in imported_modules(ast.parse(source), module.rpartition(".")[0]):
        if message := role_direction_error(module, target):
            errors.append(f"{line}: {target}: {message}")
        if module.rsplit(".", 1)[-1] in {"models", "repository"} and (
            target.split(".")[0] in {"fastapi", "starlette"}
            or (target.startswith("mosemo.") and "router" in target.split("."))
        ):
            errors.append(f"{line}: {target}: 모델·저장소에서 HTTP 계층 참조 금지")
        if module in RUNTIME_ERROR_MODULES and any(
            target == name or target.startswith(f"{name}.") for name in OPENAPI_MODULES
        ):
            errors.append(f"{line}: {target}: ADR 0001 런타임 → OpenAPI 의존 금지")
    return sorted(set(errors))


# ADR 0011: BC 패키지는 `src/mosemo/{bc}/public.py`가 있는 패키지다. 고정 목록을 두지 않고
# 있는 것만 검사한다.
LAYERS = {"presentation", "application", "domain", "infrastructure"}
CONTEXT_ROOT_FILES = {"public.py", "bootstrap.py", "__init__.py"}
CONTEXT_INIT = PurePosixPath("__init__.py")
PUBLIC_FILE = PurePosixPath("public.py")
PORTS_FILE = PurePosixPath("application/ports.py")
EVENTS_FILE = PurePosixPath("domain/events.py")
USE_CASE = "UseCase"
DOMAIN_EVENT = "DomainEvent"
TRANSACTION_HELPERS = {"unit_of_work", "read_snapshot"}
ROUTE_DECORATORS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "api_route",
}
SESSION_NAMES = {"db_session", "session", "_session"}
SESSION_TYPES = {"AsyncSession", "Session", "async_sessionmaker", "sessionmaker"}
STATELESS_SUFFIXES = ("Repository", "Query", "Queries")
QUERY_STEMS = {"query", "queries"}
QUERY_SUFFIXES = ("_query", "_queries")
QUERY_WRITES = {"insert", "update", "delete", "add", "add_all"}
# ADR 0012: 이벤트 이름의 마지막 단어로 허용하는, `-ed`로 끝나지 않는 과거분사.
# 새 이벤트에 필요하면 이 목록에 추가하며 리뷰 대상이다.
IRREGULAR_PAST_PARTICIPLES = {
    "Begun",
    "Bound",
    "Broken",
    "Built",
    "Chosen",
    "Done",
    "Drawn",
    "Found",
    "Frozen",
    "Given",
    "Hidden",
    "Held",
    "Kept",
    "Laid",
    "Left",
    "Lost",
    "Made",
    "Met",
    "Paid",
    "Put",
    "Read",
    "Rebuilt",
    "Reset",
    "Run",
    "Seen",
    "Sent",
    "Set",
    "Shown",
    "Sold",
    "Spent",
    "Split",
    "Taken",
    "Undone",
    "Withdrawn",
    "Won",
    "Written",
}
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class ContextFile:
    """BC 패키지의 파일 하나. `local`은 BC 폴더 기준 경로다."""

    path: Path
    module: str
    local: PurePosixPath
    tree: ast.Module

    @property
    def layer(self) -> str | None:
        return self.local.parts[0] if len(self.local.parts) > 1 else None

    @property
    def package(self) -> str:
        return self.module.rpartition(".")[0]


@dataclass(frozen=True)
class ContextNames:
    """BC 안에서 `UseCase`·`DomainEvent`를 (간접) 상속한 클래스 이름."""

    use_cases: set[str]
    events: set[str]


def context_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in (root / "src" / "mosemo").glob("*/public.py"))


def name_of(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return name_of(node.value)
    return None


def base_names(node: ast.ClassDef) -> set[str]:
    return {name for base in node.bases if (name := name_of(base))}


def subclass_names(files: list[ContextFile], base: str) -> set[str]:
    classes = [
        node
        for file in files
        for node in ast.walk(file.tree)
        if isinstance(node, ast.ClassDef)
    ]
    names = {base}
    changed = True
    while changed:
        found = {node.name for node in classes if base_names(node) & names}
        changed = not found <= names
        names |= found
    return names


def is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def annotation_names(node: ast.AST) -> set[str]:
    """타입 주석에서 Name, Attribute의 마지막 이름, 문자열 주석 안의 이름을 모은다."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return annotation_names(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return set()
    names = set()
    for child in ast.iter_child_nodes(node):
        names |= annotation_names(child)
    return names


def arguments_of(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    arguments = function.args
    return [
        *arguments.posonlyargs,
        *arguments.args,
        *([arguments.vararg] if arguments.vararg else []),
        *arguments.kwonlyargs,
        *([arguments.kwarg] if arguments.kwarg else []),
    ]


def check_context_root(root: Path, context: Path) -> list[str]:
    """BC 루트에 허용 목록 밖의 파일·폴더가 있는지 확인한다."""
    errors = []
    for entry in sorted(context.iterdir()):
        if entry.name == "__pycache__" or entry.name.startswith("."):
            continue
        allowed = LAYERS if entry.is_dir() else CONTEXT_ROOT_FILES
        if entry.name not in allowed:
            errors.append(
                f"{entry.relative_to(root)}: ADR 0011: BC 루트에는 `public.py`·"
                "`bootstrap.py`·`__init__.py`와 계층 폴더(`presentation`·"
                "`application`·`domain`·`infrastructure`)만 둔다. 계층 폴더로 옮긴다"
            )
    return errors


def check_root_init(file: ContextFile, names: ContextNames) -> list[str]:
    if file.local != CONTEXT_INIT:
        return []
    body = (
        file.tree.body[1:]
        if file.tree.body and is_docstring(file.tree.body[0])
        else file.tree.body
    )
    return [
        f"{node.lineno}: ADR 0011: BC 루트 `__init__.py`에는 docstring만 둔다. 여기서 "
        "내부 계층을 다시 내보내면 `public.py`를 거치지 않는 import가 생긴다. 다른 BC에 "
        "공개할 것은 `public.py`로 옮긴다"
        for node in body
    ]


def check_protocols(file: ContextFile, names: ContextNames) -> list[str]:
    if file.local == PORTS_FILE:
        return []
    return [
        f"{node.lineno}: ADR 0011: `Protocol` `{node.name}`은 외부 시스템 경계의 포트로 "
        "`application/ports.py`에만 둔다. Repository와 Query에는 포트를 두지 않는다"
        for node in ast.walk(file.tree)
        if isinstance(node, ast.ClassDef) and "Protocol" in base_names(node)
    ]


def is_use_case(node: ast.ClassDef, names: ContextNames) -> bool:
    return bool(base_names(node) & names.use_cases)


def check_use_cases(file: ContextFile, names: ContextNames) -> list[str]:
    errors = []
    for node in ast.walk(file.tree):
        if not (isinstance(node, ast.ClassDef) and is_use_case(node, names)):
            continue
        if file.layer != "application":
            errors.append(
                f"{node.lineno}: ADR 0013: UseCase `{node.name}`는 `application`에만 "
                "둔다"
            )
        public = [
            method
            for method in node.body
            if isinstance(method, FUNCTIONS) and not method.name.startswith("_")
        ]
        if [method.name for method in public] != ["execute"] or not isinstance(
            public[0], ast.AsyncFunctionDef
        ):
            errors.append(
                f"{node.lineno}: ADR 0013: UseCase `{node.name}`의 공개 메서드는 "
                "`async def execute` 하나만 둔다. 다른 처리는 private 메서드로 옮긴다"
            )
    return errors


def check_execute_owners(file: ContextFile, names: ContextNames) -> list[str]:
    if file.layer != "application":
        return []
    return [
        f"{node.lineno}: ADR 0013: application에서 `execute`를 가진 `{node.name}`는 "
        "`UseCase`를 상속한다. 공개 협력자라면 업무 이름의 메서드로 바꾼다"
        for node in ast.walk(file.tree)
        if isinstance(node, ast.ClassDef)
        and not is_use_case(node, names)
        and any(
            isinstance(method, FUNCTIONS) and method.name == "execute"
            for method in node.body
        )
    ]


def transaction_helper_lines(
    node: ast.AST, names: ContextNames, *, inside: bool
) -> list[int]:
    """`UseCase` 하위 클래스의 메서드 밖에서 트랜잭션 헬퍼를 참조하는 라인을 모은다."""
    if isinstance(node, ast.ClassDef):
        use_case = is_use_case(node, names)
        return [
            line
            for item in node.body
            for line in transaction_helper_lines(
                item, names, inside=use_case and isinstance(item, FUNCTIONS)
            )
        ]
    lines = []
    if (
        not inside
        and isinstance(node, ast.Name | ast.Attribute)
        and name_of(node) in TRANSACTION_HELPERS
    ):
        lines.append(node.lineno)
    for child in ast.iter_child_nodes(node):
        lines += transaction_helper_lines(child, names, inside=inside)
    return lines


def check_transaction_helpers(file: ContextFile, names: ContextNames) -> list[str]:
    return [
        f"{line}: ADR 0013: `unit_of_work`·`read_snapshot`은 `UseCase` 하위 클래스의 "
        "메서드 안에서만 쓴다. 트랜잭션을 여는 코드를 UseCase로 옮긴다"
        for line in transaction_helper_lines(file.tree, names, inside=False)
    ]


def is_route(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in ROUTE_DECORATORS
        for decorator in function.decorator_list
    )


def is_execute_call(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and name_of(node.func.value) not in SESSION_NAMES
    )


def check_router_calls(file: ContextFile, names: ContextNames) -> list[str]:
    if file.layer != "presentation":
        return []
    return [
        f"{node.lineno}: ADR 0013: 라우터 `{function.name}`는 UseCase의 `execute`만 "
        "호출(await)한다. Repository·Query·세션·공개 협력자 호출은 UseCase로 옮긴다"
        for function in ast.walk(file.tree)
        if isinstance(function, FUNCTIONS) and is_route(function)
        for node in ast.walk(function)
        if isinstance(node, ast.Await) and not is_execute_call(node.value)
    ]


def is_session_annotation(node: ast.expr) -> bool:
    """주석이 `AsyncSession` 자체이거나 `None`과의 합집합·`Optional`인지 판정한다.

    `async_sessionmaker[AsyncSession]`처럼 세션을 만드는 쪽의 타입은 대상이 아니다.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return is_session_annotation(ast.parse(node.value, mode="eval").body)
        except SyntaxError:
            return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        sides = [node.left, node.right]
        return any(is_session_annotation(side) for side in sides) and all(
            is_session_annotation(side)
            or (isinstance(side, ast.Constant) and side.value is None)
            for side in sides
        )
    if isinstance(node, ast.Subscript) and name_of(node.value) == "Optional":
        return is_session_annotation(node.slice)
    return (
        isinstance(node, ast.Name | ast.Attribute) and name_of(node) == "AsyncSession"
    )


def check_session_parameters(file: ContextFile, names: ContextNames) -> list[str]:
    return [
        f"{argument.lineno}: ADR 0013: `AsyncSession` 매개변수 `{argument.arg}`의 "
        "이름을 `db_session`으로 바꾼다"
        for function in ast.walk(file.tree)
        if isinstance(function, FUNCTIONS)
        for argument in arguments_of(function)
        if argument.annotation is not None
        and is_session_annotation(argument.annotation)
        and argument.arg != "db_session"
    ]


def check_stateless_constructors(file: ContextFile, names: ContextNames) -> list[str]:
    return [
        f"{argument.lineno}: ADR 0013: `{node.name}`는 생성자에서 세션을 받지 않는다. "
        "메서드 첫 인자 `db_session: AsyncSession`으로 받는다"
        for node in ast.walk(file.tree)
        if isinstance(node, ast.ClassDef) and node.name.endswith(STATELESS_SUFFIXES)
        for method in node.body
        if isinstance(method, FUNCTIONS) and method.name == "__init__"
        for argument in arguments_of(method)[1:]
        if argument.arg in SESSION_NAMES
        or (
            argument.annotation is not None
            and annotation_names(argument.annotation) & SESSION_TYPES
        )
    ]


def is_query_file(file: ContextFile) -> bool:
    stem = file.local.stem
    return file.layer == "infrastructure" and (
        stem in QUERY_STEMS or stem.endswith(QUERY_SUFFIXES)
    )


def check_query_writes(file: ContextFile, names: ContextNames) -> list[str]:
    if not is_query_file(file):
        return []
    found = []
    for node in ast.walk(file.tree):
        if isinstance(node, ast.Call) and name_of(node.func) in QUERY_WRITES:
            found.append((node.lineno, name_of(node.func)))
        if isinstance(node, ast.ImportFrom):
            found.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if alias.name in QUERY_WRITES
            )
    return [
        f"{line}: ADR 0013: Query 파일에서 `{name}`을 쓰지 않는다. Query는 읽기 모델만 "
        "읽고, 쓰기는 Repository로 옮긴다"
        for line, name in found
    ]


def is_frozen_dataclass(node: ast.ClassDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and name_of(decorator.func) == "dataclass"
        and any(
            keyword.arg == "frozen"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in decorator.keywords
        )
        for decorator in node.decorator_list
    )


def event_name_error(name: str) -> str | None:
    if name.endswith("Event"):
        return f"이벤트 `{name}`에 `Event` 접미사를 붙이지 않는다"
    words = re.findall(r"[A-Z][a-z0-9]*", name)
    last = words[-1] if words else name
    if last.endswith("ed") or last in IRREGULAR_PAST_PARTICIPLES:
        return None
    return (
        f"이벤트 `{name}`는 `{{대상 명사}}{{과거분사}}`로 짓는다. 마지막 단어가 `-ed`로 "
        "끝나거나 검사기의 불규칙 과거분사 목록 `IRREGULAR_PAST_PARTICIPLES`에 있어야 한다"
    )


def check_event_class(node: ast.ClassDef, file: ContextFile) -> list[str]:
    errors = []
    if file.local != EVENTS_FILE:
        errors.append(f"이벤트 `{node.name}`는 `domain/events.py`에만 둔다")
    if not is_frozen_dataclass(node):
        errors.append(f"이벤트 `{node.name}`는 `@dataclass(frozen=True)`로 선언한다")
    if message := event_name_error(node.name):
        errors.append(message)
    return [f"{node.lineno}: ADR 0012: {error}" for error in errors]


def check_events(file: ContextFile, names: ContextNames) -> list[str]:
    errors = []
    for node in ast.walk(file.tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if base_names(node) & names.events:
            errors += check_event_class(node, file)
        elif file.local == EVENTS_FILE:
            errors.append(
                f"{node.lineno}: ADR 0012: `domain/events.py`의 `{node.name}`는 "
                "`DomainEvent`를 상속한다. 이벤트가 아니면 다른 domain 파일로 옮긴다"
            )
    return errors


def is_events_append(node: ast.stmt) -> bool:
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "append"
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "events"
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "self"
    )


def check_mark_methods(file: ContextFile, names: ContextNames) -> list[str]:
    if file.layer != "domain":
        return []
    return [
        f"{statement.lineno}: ADR 0012: `{function.name}`는 `self.events.append(...)`로 "
        "이벤트만 추가하고 상태를 바꾸지 않는다. 상태 변경은 다른 aggregate 메서드로 "
        "옮긴다"
        for function in ast.walk(file.tree)
        if isinstance(function, FUNCTIONS) and function.name.startswith("mark_")
        for index, statement in enumerate(function.body)
        if not (index == 0 and is_docstring(statement))
        and not is_events_append(statement)
    ]


def domain_import_names(file: ContextFile, context_module: str) -> set[str]:
    names = set()
    for node in ast.walk(file.tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if node.level:
            module = resolve_name("." * node.level + module, file.package)
        if module.startswith(f"{context_module}.domain"):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def check_domain_attributes(file: ContextFile, context_module: str) -> list[str]:
    """infrastructure에서 domain 클래스 속성으로 SQL 식을 만드는지 찾는다."""
    if file.layer != "infrastructure":
        return []
    domain_names = domain_import_names(file, context_module)
    called = {
        id(node.func) for node in ast.walk(file.tree) if isinstance(node, ast.Call)
    }
    return [
        f"{node.lineno}: ADR 0013: infrastructure의 SQL 식은 `Table` 컬럼"
        f"(`table.c.{node.attr}`)으로 쓴다. domain 클래스 속성 "
        f"`{node.value.id}.{node.attr}`를 쓰지 않는다"
        for node in ast.walk(file.tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in domain_names
        and not node.attr.startswith("__")
        and not node.attr.isupper()
        and id(node) not in called
    ]


def class_in(file: ContextFile, name: str) -> ast.ClassDef | None:
    return next(
        (
            node
            for node in file.tree.body
            if isinstance(node, ast.ClassDef) and node.name == name
        ),
        None,
    )


def public_collaborators(
    files: list[ContextFile], context_module: str
) -> list[tuple[ContextFile, ast.ClassDef]]:
    """`public.py`가 application 모듈에서 가져와 내보내는 클래스를 찾는다."""
    public = next((file for file in files if file.local == PUBLIC_FILE), None)
    if public is None:
        return []
    modules = {file.module.removesuffix(".__init__"): file for file in files}
    found = []
    for node in ast.walk(public.tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if node.level:
            module = resolve_name("." * node.level + module, context_module)
        source = modules.get(module)
        if source is None or source.layer != "application":
            continue
        found.extend(
            located
            for alias in node.names
            if (located := find_class(modules, source, name=alias.name)) is not None
        )
    return found


def find_class(
    modules: dict[str, ContextFile], source: ContextFile, *, name: str, depth: int = 5
) -> tuple[ContextFile, ast.ClassDef] | None:
    """`source`에 정의됐거나 `source`가 다시 내보내는 클래스를 정의 위치와 함께 찾는다."""
    if (cls := class_in(source, name)) is not None:
        return source, cls
    if depth == 0:
        return None
    for node in source.tree.body:
        if not isinstance(node, ast.ImportFrom) or all(
            alias.name != name for alias in node.names
        ):
            continue
        module = node.module or ""
        if node.level:
            module = resolve_name("." * node.level + module, source.package)
        if (target := modules.get(module)) is not None:
            return find_class(modules, target, name=name, depth=depth - 1)
    return None


def check_public_collaborators(
    files: list[ContextFile], context_module: str, *, names: ContextNames
) -> list[str]:
    errors = []
    for file, node in public_collaborators(files, context_module):
        problems = []
        if is_use_case(node, names):
            problems.append("`UseCase`를 상속하지 않는다")
        if any(
            isinstance(method, FUNCTIONS) and method.name == "execute"
            for method in node.body
        ):
            problems.append("`execute` 대신 업무 이름의 메서드를 둔다")
        if any(
            isinstance(child, ast.Name | ast.Attribute)
            and name_of(child) in TRANSACTION_HELPERS
            for child in ast.walk(node)
        ):
            problems.append(
                "`unit_of_work`·`read_snapshot`을 쓰지 않고 호출한 UseCase의 "
                "트랜잭션 안에서 실행된다"
            )
        errors.extend(
            f"{file.path}:{node.lineno}: ADR 0013: 공개 협력자 `{node.name}`는 "
            f"{problem}"
            for problem in problems
        )
    return errors


FILE_RULES = (
    check_root_init,
    check_protocols,
    check_use_cases,
    check_execute_owners,
    check_transaction_helpers,
    check_router_calls,
    check_session_parameters,
    check_stateless_constructors,
    check_query_writes,
    check_events,
    check_mark_methods,
)


def parse_context(root: Path, context: Path) -> tuple[list[ContextFile], list[str]]:
    files = []
    errors = []
    for path in sorted(context.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as error:
            errors.append(f"{path.relative_to(root)}: 코드 분석 실패: {error}")
            continue
        files.append(
            ContextFile(
                path=path.relative_to(root),
                module=".".join(path.relative_to(root / "src").with_suffix("").parts),
                local=PurePosixPath(path.relative_to(context).as_posix()),
                tree=tree,
            )
        )
    return files, errors


def validate_context(root: Path, context: Path) -> list[str]:
    """BC 패키지 하나에 ADR 0011~0013의 AST 규칙을 적용한다."""
    context_module = f"mosemo.{context.name}"
    files, errors = parse_context(root, context)
    names = ContextNames(
        use_cases=subclass_names(files, USE_CASE),
        events=subclass_names(files, DOMAIN_EVENT),
    )
    errors += check_context_root(root, context)
    for file in files:
        file_errors = [error for rule in FILE_RULES for error in rule(file, names)]
        file_errors += check_domain_attributes(file, context_module)
        errors.extend(f"{file.path}:{error}" for error in file_errors)
    return errors + check_public_collaborators(files, context_module, names=names)


def validate(root: Path = ROOT) -> list[str]:
    contexts = context_dirs(root)
    errors = [
        error for context in contexts for error in validate_context(root, context)
    ]
    for path in sorted((root / "src" / "mosemo").rglob("*.py")):
        if any(path.is_relative_to(context) for context in contexts):
            continue  # 옛 역할 파일 규칙(CS-P32 등)은 BC 밖의 옛 경로에만 둔다.
        module = ".".join(path.relative_to(root / "src").with_suffix("").parts)
        try:
            errors.extend(
                f"{path.relative_to(root)}:{error}"
                for error in check_source(path.read_text(), module)
            )
        except (SyntaxError, ImportError, ValueError) as error:
            errors.append(f"{path.relative_to(root)}: import 분석 실패: {error}")
    if not (root / "src" / "mosemo" / "api" / "main.py").is_file():
        errors.append("애플리케이션 진입점 없음: src/mosemo/api/main.py")
    return errors


if __name__ == "__main__":
    failures = validate()
    print("\n".join(failures) if failures else "아키텍처 검사 통과")
    raise SystemExit(bool(failures))

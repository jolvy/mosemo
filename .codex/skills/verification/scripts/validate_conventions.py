"""현재 시각 처리, 검사 억제, 트랜잭션·예외 경계에 관한 코드 규칙을 AST·주석 토큰·Ruff 설정으로 검사한다."""

import ast
import io
import os
import re
import tokenize
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PYTHON_DIRS = ("src", "tests", "scripts", "migrations", ".codex")
SKIPPED_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".ruff_cache"}
RUFF_CONFIG_FILES = {"ruff.toml", ".ruff.toml"}
COMPLEXIPY_CONFIG_FILE = "complexipy.toml"
COMPLEXITY_SNAPSHOT = "complexipy-snapshot.json"
EXCEPTIONS_FILE = "src/mosemo/shared/errors.py"
API_EXCEPTION = "ApiException"
API_EXCEPTION_FILES = {
    "src/mosemo/api/exception_handlers.py",
    EXCEPTIONS_FILE,
}
HTTP_EXCEPTION = "HTTPException"
HTTP_EXCEPTION_FILE = "src/mosemo/api/exception_handlers.py"
BROAD_EXCEPTIONS = {"Exception", "BaseException"}
# (파일, 감싸는 클래스·함수 이름): 허용 개수. 새 경계는 이유와 함께 추가하고 리뷰한다.
BROAD_EXCEPT_ALLOWED = {
    # 프로세스 경계: 예외 메시지(payload·비밀값)를 출력하지 않고 종료
    ("src/mosemo/consumer/__main__.py", "main"): 1,
    # 프로세스 경계
    ("src/mosemo/batch/__main__.py", "main"): 1,
    # 오류를 runtime supervisor로 넘김
    ("src/mosemo/platform/messaging/subscriber.py", "MessageConsumer.handle"): 1,
    # 어떤 오류든 앱으로 실패 리다이렉트
    ("src/mosemo/identity/presentation/auth/router.py", "create_router.callback"): 1,
    # 후보 하나의 제안 생성 실패는 시도 실패로 기록하고 스캐너를 멈추지 않음
    ("src/mosemo/labeling/application/proposals.py", "ProposeLabel.execute"): 1,
    # job 하나의 실패가 스케줄러를 멈추지 않고, 예외 메시지를 로그에 남기지 않음
    ("src/mosemo/platform/scheduling.py", "execute_job"): 1,
}
RUFF_CONFIG = tomllib.loads("""
line-length = 88
target-version = "py314"

[lint]
extend-select = ["I", "TID251", "DTZ", "RET505", "SIM102", "PLR5501", "SIM103", "PLR0917",
                 "FBT001", "FBT003", "PLR2004", "N", "FAST002", "ASYNC", "ERA001", "S110", "SIM105", "PT",
                 "PLR0913"]

[lint.per-file-ignores]
"src/mosemo/platform/clock.py" = ["TID251"]
"src/mosemo/api/middleware.py" = ["PLR0917"]
"tests/**" = ["PLR2004", "FBT", "ASYNC", "PLR0917", "PLR0913"]
"scripts/**" = ["PLR2004"]
"migrations/**" = ["PLR2004"]
".codex/**" = ["PLR2004"]
"src/**" = ["BLE001"]

[lint.pylint]
max-positional-args = 2

[lint.pep8-naming]
extend-ignore-names = ["*Exception"]

[lint.flake8-tidy-imports.banned-api]
"datetime.datetime.now".msg = "CS-P15: 현재 시각은 `mosemo.platform.clock`의 clock을 주입받아 얻는다."
"datetime.datetime.utcnow".msg = "CS-P15: 현재 시각은 `mosemo.platform.clock`의 clock을 주입받아 얻는다."
"datetime.datetime.today".msg = "CS-P15: 현재 시각은 `mosemo.platform.clock`의 clock을 주입받아 얻는다."
"datetime.date.today".msg = "CS-P15: 오늘 날짜는 `mosemo.platform.clock`의 clock을 주입받아 얻은 시각에서 계산한다."
"time.time".msg = "CS-P15: 현재 시각은 `mosemo.platform.clock`의 clock을 주입받아 얻는다."
"time.time_ns".msg = "CS-P15: 현재 시각은 `mosemo.platform.clock`의 clock을 주입받아 얻는다."
"sqlalchemy.orm.DeclarativeBase".msg = "ADR 0013: 엔티티는 domain dataclass로 두고 BC infrastructure의 `Table`과 imperative mapping으로 매핑한다."
"sqlalchemy.orm.mapped_column".msg = "ADR 0013: 컬럼은 BC infrastructure의 `Table`에 `Column`으로 정의한다."

[format]
indent-style = "space"
line-ending = "auto"
quote-style = "double"
""")
DEPENDENCY_NAMES = {"AsyncSession", "Session", "async_sessionmaker", "sessionmaker"}
DEPENDENCY_SUFFIXES = (
    "Repository",
    "Service",
    "Clock",
    "Config",
    "Client",
    "Resolver",
    "Mapper",
    "Queries",
    "Commands",
    "Provisioner",
    "Processor",
    "Scanner",
    "Suggester",
)
OVERRIDE_MODULES = {"typing", "typing_extensions"}
TRANSACTION_BOUNDARY_METHODS = {"begin", "begin_nested", "commit", "rollback"}
TRANSACTION_HELPER_FILE = "src/mosemo/platform/database/transactions.py"

CLOCK_HINT = (
    "현재 시각은 `mosemo.platform.clock`의 clock을 주입받아 얻고, "
    "Ruff TID251·DTZ 검사를 끄거나 우회하지 않는다"
)
SUPPRESSION_DIRECTIVE = re.compile(
    r"(?<!\w)(?:"
    r"(?:(?:ruff\s*:\s*)?noqa"
    r"|(?:type|ty|pyright|complexipy)\s*:\s*ignore"
    r"|pragma\s*:\s*no\s*cover)(?!\w)"
    r"|isort\s*:"
    r"|fmt\s*:"
    r")",
    re.IGNORECASE,
)


def is_lambda_returning_lambda(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Lambda) and isinstance(node.body, ast.Lambda)


def is_get_clock(node: ast.AST | None) -> bool:
    return (isinstance(node, ast.Name) and node.id == "get_clock") or (
        isinstance(node, ast.Attribute) and node.attr == "get_clock"
    )


def is_dependency_overrides(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "dependency_overrides") or (
        isinstance(node, ast.Attribute) and node.attr == "dependency_overrides"
    )


def check_test_clock_lambdas(tree: ast.AST) -> list[str]:
    """(a) 테스트에서 `clock=`에 lambda를 넘기는 호출을 찾는다."""
    return [
        f"{keyword.value.lineno}: CS-P23: `clock=`에 lambda를 넘기지 않는다. "
        "`tests/clock_support.py`의 `FakeClock` 인스턴스나 `utc_now`를 넘긴다"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "clock" and isinstance(keyword.value, ast.Lambda)
    ]


def check_clock_overrides(tree: ast.AST) -> list[str]:
    """(b) `dependency_overrides`의 `get_clock`에 lambda를 돌려주는 lambda를 찾는다."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if is_lambda_returning_lambda(node.value) and any(
                isinstance(target, ast.Subscript)
                and is_dependency_overrides(target.value)
                and is_get_clock(target.slice)
                for target in targets
            ):
                lines.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and is_dependency_overrides(node.func.value)
        ):
            lines.extend(
                value.lineno
                for argument in node.args
                if isinstance(argument, ast.Dict)
                for key, value in zip(argument.keys, argument.values, strict=True)
                if is_get_clock(key) and is_lambda_returning_lambda(value)
            )
    return [
        f"{line}: CS-P23: `get_clock` 재정의에 lambda를 돌려주는 lambda를 넣지 않는다. "
        "`lambda: fake_clock`처럼 clock 객체를 돌려준다"
        for line in lines
    ]


def check_suppression_comments(source: str) -> list[str]:
    """(i) 주석 토큰에서 검사를 끄거나 우회하는 지시문을 찾는다."""
    errors = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        errors.extend(
            f"{token.start[0]}: CS-P22: 검사 억제 주석 `{match.group(0)}`을 쓰지 "
            "않는다. 주석을 지우고 지적된 코드를 고친다. 경로 단위 예외가 필요하면 "
            "검사기의 허용 Ruff 설정을 바꿔야 하며 리뷰 대상이다. "
            f"{CLOCK_HINT}"
            for match in SUPPRESSION_DIRECTIVE.finditer(token.string)
        )
    return errors


def ruff_config_differences(
    actual: object, expected: object, *, section: str
) -> list[str]:
    if not (isinstance(actual, dict) and isinstance(expected, dict)):
        if actual == expected:
            return []
        return [f"`{section}` 값을 `{expected!r}`로 되돌린다"]
    differences = [
        f"`{section}.{key}`를 삭제한다" for key in actual if key not in expected
    ]
    differences.extend(
        f"`{section}.{key}`를 `{expected[key]!r}`로 되살린다"
        for key in expected
        if key not in actual
    )
    for key in expected:
        if key in actual:
            differences += ruff_config_differences(
                actual[key], expected[key], section=f"{section}.{key}"
            )
    return differences


def check_ruff_config(pyproject: str) -> list[str]:
    """(j) pyproject.toml의 Ruff 설정이 허용 설정과 키 집합·값 모두 같은지 확인한다."""
    ruff = tomllib.loads(pyproject).get("tool", {}).get("ruff", {})
    return [
        f"pyproject.toml: CS-P22: Ruff 설정은 검사기의 허용 설정과 같아야 한다. "
        f"{difference}. 규칙 선택·경로 예외·제외 경로를 바꿔 검사를 끄거나 "
        "우회하지 않는다. 설정 변경이 필요하면 검사기 `RUFF_CONFIG`를 함께 바꿔야 "
        f"하며 리뷰 대상이다. {CLOCK_HINT}"
        for difference in ruff_config_differences(
            ruff, RUFF_CONFIG, section="tool.ruff"
        )
    ]


def check_init_clock(tree: ast.AST) -> list[str]:
    """(e) `__init__`의 `clock` 매개변수가 기본값 없는 키워드 전용인지 확인한다."""
    errors = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "__init__"
        ):
            continue
        arguments = node.args
        positional = [*arguments.posonlyargs, *arguments.args]
        defaulted = [
            argument.arg
            for argument, default in zip(
                arguments.kwonlyargs, arguments.kw_defaults, strict=True
            )
            if default is not None
        ]
        if any(argument.arg == "clock" for argument in positional):
            errors.append(
                f"{node.lineno}: CS-P15: `__init__`의 `clock`은 키워드 전용으로 받는다. "
                "`*, clock: Clock`으로 선언한다"
            )
        if "clock" in defaulted:
            errors.append(
                f"{node.lineno}: CS-P15: `__init__`의 `clock`에 기본값을 두지 않는다. "
                "호출하는 쪽이 clock을 주입한다"
            )
    return errors


def integrity_error_aliases(tree: ast.AST) -> set[str]:
    return {"IntegrityError"} | {
        alias.asname
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "IntegrityError" and alias.asname
    }


def is_integrity_error(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Tuple):
        return any(is_integrity_error(element, aliases) for element in node.elts)
    return (isinstance(node, ast.Name) and node.id in aliases) or (
        isinstance(node, ast.Attribute) and node.attr == "IntegrityError"
    )


def check_integrity_error_handling(tree: ast.AST) -> list[str]:
    """(g) `IntegrityError`를 잡는 `except` 절을 찾는다."""
    aliases = integrity_error_aliases(tree)
    return [
        f"{node.lineno}: CS-P26: `IntegrityError`를 잡아 중복을 처리하지 않는다. "
        "Repository의 `get_or_create`에서 `on_conflict_do_nothing`으로 충돌을 "
        "피하고 반환값으로 중복을 판단한다"
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and node.type is not None
        and is_integrity_error(node.type, aliases)
    ]


def check_transaction_calls(tree: ast.AST) -> list[str]:
    """(h) 트랜잭션 헬퍼 밖에서 트랜잭션을 직접 열거나 확정하는 호출을 찾는다."""
    return [
        f"{node.lineno}: CS-P13: `{TRANSACTION_HELPER_FILE}` 밖에서는 `begin`·"
        "`begin_nested`·`commit`·`rollback`을 직접 호출하지 않는다. 트랜잭션은 "
        "UseCase가 `platform.database.transactions`의 `unit_of_work`·`read_snapshot`으로 연다"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in TRANSACTION_BOUNDARY_METHODS
    ]


def is_broad_exception(node: ast.expr | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Tuple):
        return any(is_broad_exception(element) for element in node.elts)
    return (isinstance(node, ast.Name) and node.id in BROAD_EXCEPTIONS) or (
        isinstance(node, ast.Attribute) and node.attr in BROAD_EXCEPTIONS
    )


def broad_except_lines(
    node: ast.AST, scope: tuple[str, ...] = ()
) -> list[tuple[str, int]]:
    """넓은 `except` 절을 감싸는 클래스·함수 이름과 라인으로 모은다."""
    found = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            found += broad_except_lines(child, (*scope, child.name))
            continue
        if isinstance(child, ast.ExceptHandler) and is_broad_exception(child.type):
            found.append((".".join(scope) or "<module>", child.lineno))
        found += broad_except_lines(child, scope)
    return found


def check_broad_excepts(trees: dict[str, ast.AST]) -> list[str]:
    """(k) `src`의 넓은 `except`가 허용 목록의 (파일, 함수)와 개수 안에 있는지 확인한다."""
    errors = []
    counts = dict.fromkeys(BROAD_EXCEPT_ALLOWED, 0)
    for path, tree in trees.items():
        for function, line in broad_except_lines(tree):
            key = (path, function)
            counts[key] = counts.get(key, 0) + 1
            if counts[key] > BROAD_EXCEPT_ALLOWED.get(key, 0):
                errors.append(
                    f"{path}:{line}: CS-P18: `{function}`의 넓은 `except`"
                    "(`Exception`·`BaseException`·맨 `except:`)는 등록된 경계에서만 "
                    "쓴다. 잡을 예외를 좁혀라. 새 경계가 필요하면 이유와 함께 검사기 "
                    "허용 목록 `BROAD_EXCEPT_ALLOWED`에 추가해야 하며 리뷰 대상이다"
                )
    errors.extend(
        f"{path}: CS-P18: 허용 목록의 `{function}`에 넓은 `except`가 "
        f"{allowed}개 있어야 하지만 {counts[path, function]}개다. 경계가 사라지거나 "
        "옮겨졌으면 검사기 허용 목록 `BROAD_EXCEPT_ALLOWED`를 코드와 맞춘다"
        for (path, function), allowed in BROAD_EXCEPT_ALLOWED.items()
        if counts[path, function] < allowed
    )
    return errors


def check_complexity_snapshot(root: Path) -> list[str]:
    """(l) 저장소 루트에 complexipy 기준선 파일이 없는지 확인한다."""
    if not (root / COMPLEXITY_SNAPSHOT).is_file():
        return []
    return [
        (
            f"{COMPLEXITY_SNAPSHOT}: CS-P01: complexipy 기준선 파일을 두지 않는다. "
            "이 파일을 삭제하고 인지 복잡도 15를 넘는 함수를 나눈다. "
            "`--snapshot-create`로 기준선을 만들지 않는다"
        )
    ]


def check_complexity_config(pyproject: str) -> list[str]:
    """(l) pyproject.toml에 complexipy 설정을 두지 않는지 확인한다."""
    if "complexipy" not in tomllib.loads(pyproject).get("tool", {}):
        return []
    return [
        (
            "pyproject.toml: CS-P01: `[tool.complexipy]`를 삭제한다. complexipy는 "
            "고정 명령 `complexipy src --max-complexity-allowed 15 --isolated "
            "--no-ignore`로만 실행하고 설정으로 대상을 제외하거나 기준을 바꾸지 않는다"
        )
    ]


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


def is_dependency_name(name: str) -> bool:
    return name in DEPENDENCY_NAMES or name.endswith(DEPENDENCY_SUFFIXES)


def is_bundle_class(node: ast.ClassDef) -> bool:
    decorators = [
        decorator.func if isinstance(decorator, ast.Call) else decorator
        for decorator in node.decorator_list
    ]
    return any(is_named(decorator, {"dataclass"}) for decorator in decorators) or any(
        is_named(base, {"NamedTuple"}) for base in node.bases
    )


def check_dependency_bundles(tree: ast.AST) -> list[str]:
    """(o) dataclass·NamedTuple 필드의 타입이 의존성인지 확인한다."""
    return [
        f"{field.lineno}: CS-P27: `{node.name}`의 필드에 의존성 `{name}`을 담지 "
        "않는다. 의존성을 묶은 객체로 인자 개수를 줄이지 말고, 책임을 나눠 각 "
        "협력자가 필요한 의존성만 생성자로 받게 한다. 묶음 객체는 업무 사건의 "
        "데이터를 담는 값 객체에만 쓴다"
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and is_bundle_class(node)
        for field in node.body
        if isinstance(field, ast.AnnAssign)
        for name in sorted(annotation_names(field.annotation))
        if is_dependency_name(name)
    ]


def override_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """`override` 데코레이터 이름과 `typing`·`typing_extensions` 모듈 별칭을 모은다."""
    names = {"override"}
    modules = set(OVERRIDE_MODULES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in OVERRIDE_MODULES:
            names.update(
                alias.asname
                for alias in node.names
                if alias.name == "override" and alias.asname
            )
        if isinstance(node, ast.Import):
            modules.update(
                alias.asname
                for alias in node.names
                if alias.name in OVERRIDE_MODULES and alias.asname
            )
    return names, modules


def is_override(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: tuple[set[str], set[str]],
) -> bool:
    names, modules = aliases
    return any(
        (isinstance(decorator, ast.Name) and decorator.id in names)
        or (
            isinstance(decorator, ast.Attribute)
            and decorator.attr == "override"
            and isinstance(decorator.value, ast.Name)
            and decorator.value.id in modules
        )
        for decorator in function.decorator_list
    )


def check_constructor_override(tree: ast.AST) -> list[str]:
    """(r) `__init__`·`__new__`에 `typing.override` 데코레이터가 있는지 확인한다."""
    aliases = override_aliases(tree)
    return [
        f"{node.lineno}: CS-P31: 생성자 `{node.name}`에 `@override`를 붙이지 "
        "않는다. Ruff `PLR0913`·`PLR0917`이 `@override` 메서드를 건너뛰므로 "
        "인자 수 규칙을 피하게 된다. 데코레이터를 지우고 인자가 많으면 책임을 나눈다"
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name in {"__init__", "__new__"}
        and is_override(node, aliases)
    ]


def base_names(node: ast.ClassDef) -> set[str]:
    return {
        base.id if isinstance(base, ast.Name) else base.attr
        for base in node.bases
        if isinstance(base, ast.Name | ast.Attribute)
    }


def api_exception_names(tree: ast.AST) -> set[str]:
    """`ApiException`과 같은 파일에 정의된 그 하위 클래스 이름을 모은다."""
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    names = {API_EXCEPTION}
    changed = True
    while changed:
        found = {node.name for node in classes if base_names(node) & names}
        changed = not found <= names
        names |= found
    return names


def check_exception_classes(tree: ast.AST, *, in_exceptions_file: bool) -> list[str]:
    """(m) 이름이 `Exception`으로 끝나는 클래스가 `ApiException` 계층인지 확인한다."""
    allowed = api_exception_names(tree) if in_exceptions_file else set()
    return [
        f"{node.lineno}: CS-P07: `{node.name}`처럼 `Exception`으로 끝나는 이름은 "
        f"`{EXCEPTIONS_FILE}`의 `ApiException`과 그 하위 클래스에만 쓴다. "
        "도메인·인프라 오류는 `...Error`로 이름 짓고, API 오류 응답은 router에서 "
        "`ApiException`으로 바꾼다"
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name.endswith("Exception")
        and node.name not in allowed
    ]


def is_named(node: ast.AST | None, names: set[str]) -> bool:
    return (isinstance(node, ast.Name) and node.id in names) or (
        isinstance(node, ast.Attribute) and node.attr in names
    )


def check_api_exception_usage(tree: ast.AST, names: set[str]) -> list[str]:
    """(n) 허용 파일 밖에서 `ApiException` 계층의 import와 생성을 찾는다."""
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.ImportFrom)
            and any(alias.name in names for alias in node.names)
        )
        or (isinstance(node, ast.Call) and is_named(node.func, names))
        or (isinstance(node, ast.Raise) and is_named(node.exc, names))
    ]
    return [
        f"{line}: CS-P07: `ApiException`은 `router.py`와 "
        "`exception_handlers.py`·`shared/errors.py`에서만 import하거나 만든다. "
        "UseCase·Repository는 도메인 오류(`...Error`)를 던지고, router에서 "
        "`ApiException`으로 바꾼다"
        for line in lines
    ]


def check_http_exception_usage(tree: ast.AST) -> list[str]:
    """(t) `HTTPException`으로 끝나는 이름의 import와 속성 참조를 찾는다."""
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.ImportFrom)
            and any(alias.name.endswith(HTTP_EXCEPTION) for alias in node.names)
        )
        or (isinstance(node, ast.Attribute) and node.attr.endswith(HTTP_EXCEPTION))
    ]
    return [
        f"{line}: CS-P12: `HTTPException`은 `exception_handlers.py`에서만 다룬다. "
        "공개할 오류는 BC 오류 코드와 `ApiException`으로, 내부 오류는 도메인 "
        "오류(`...Error`)로 던진다"
        for line in lines
    ]


def python_files(root: Path) -> list[Path]:
    files = []
    for directory in PYTHON_DIRS:
        files.extend(
            path
            for path in (root / directory).rglob("*.py")
            if not SKIPPED_DIRS.intersection(path.relative_to(root).parts)
        )
    return sorted(files)


def config_files(root: Path, names: set[str]) -> list[Path]:
    found = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [
            name for name in subdirectories if name not in SKIPPED_DIRS
        ]
        found.extend(Path(directory) / name for name in files if name in names)
    return sorted(found)


def parse_python_files(root: Path) -> tuple[dict[Path, ast.AST], list[str]]:
    trees = {}
    errors = []
    for path in python_files(root):
        relative = path.relative_to(root)
        source = path.read_text()
        try:
            trees[relative] = ast.parse(source)
            file_errors = check_suppression_comments(source)
        except (SyntaxError, tokenize.TokenError) as error:
            errors.append(f"{relative}: 코드 분석 실패: {error}")
            continue
        errors.extend(f"{relative}:{error}" for error in file_errors)
    return trees, errors


def check_src_rules(relative: Path, tree: ast.AST) -> list[str]:
    """`src` 파일 규칙. 파일 이름으로 역할을 정하는 규칙은 두지 않는다."""
    errors = (
        check_dependency_bundles(tree)
        + check_init_clock(tree)
        + check_integrity_error_handling(tree)
    )
    if relative.as_posix() != TRANSACTION_HELPER_FILE:
        errors += check_transaction_calls(tree)
    return errors


def validate(root: Path = ROOT) -> list[str]:
    trees, errors = parse_python_files(root)
    exceptions_tree = trees.get(Path(EXCEPTIONS_FILE))
    api_names = (
        api_exception_names(exceptions_tree) if exceptions_tree else {API_EXCEPTION}
    )
    for relative, tree in trees.items():
        file_errors = (
            check_clock_overrides(tree)
            + check_exception_classes(
                tree, in_exceptions_file=relative.as_posix() == EXCEPTIONS_FILE
            )
            + check_constructor_override(tree)
        )
        if relative.parts[0] == "tests":
            file_errors += check_test_clock_lambdas(tree)
        if relative.parts[0] == "src":
            file_errors += check_src_rules(relative, tree)
            if (
                relative.name != "router.py"
                and relative.as_posix() not in API_EXCEPTION_FILES
            ):
                file_errors += check_api_exception_usage(tree, api_names)
            if relative.as_posix() != HTTP_EXCEPTION_FILE:
                file_errors += check_http_exception_usage(tree)
        errors.extend(f"{relative}:{error}" for error in file_errors)
    errors += check_broad_excepts(
        {
            relative.as_posix(): tree
            for relative, tree in trees.items()
            if relative.parts[0] == "src"
        }
    )
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        errors += check_ruff_config(pyproject.read_text())
        errors += check_complexity_config(pyproject.read_text())
    errors += check_complexity_snapshot(root)
    errors.extend(
        f"{path.relative_to(root)}: CS-P22: Ruff 설정은 `pyproject.toml` 하나에 둔다. "
        f"이 파일을 삭제한다. {CLOCK_HINT}"
        for path in config_files(root, RUFF_CONFIG_FILES)
    )
    errors.extend(
        f"{path.relative_to(root)}: CS-P01: complexipy 설정 파일을 두지 않는다. "
        "이 파일을 삭제하고 고정 명령으로만 실행한다"
        for path in config_files(root, {COMPLEXIPY_CONFIG_FILE})
    )
    return errors


if __name__ == "__main__":
    failures = validate()
    print("\n".join(failures) if failures else "코드 규칙 검사 통과")
    raise SystemExit(bool(failures))

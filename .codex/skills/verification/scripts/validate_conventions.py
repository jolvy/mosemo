"""현재 시각 처리와 검사 억제에 관한 코드 규칙을 AST·주석 토큰·Ruff 설정으로 검사한다."""

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
CLOCK_EXCEPTION = ("src/mosemo/clock.py", "TID251")

CLOCK_HINT = (
    "현재 시각은 `mosemo.clock`의 clock을 주입받아 얻고, "
    "Ruff TID251·DTZ 검사를 끄거나 우회하지 않는다"
)
NOQA = re.compile(r"(?<![a-z0-9_])noqa(?![a-z0-9_])", re.IGNORECASE)
NOQA_CODES = re.compile(r"\s*:\s*([a-z]+[0-9]*(?:[\s,]+[a-z]+[0-9]*)*)", re.IGNORECASE)
FILE_NOQA = re.compile(r"ruff\s*:\s*noqa", re.IGNORECASE)


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
    """(c) 주석 토큰에서 전체 억제와 TID251·DTZ 억제를 찾는다."""
    errors = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        line = token.start[0]
        if FILE_NOQA.search(token.string):
            errors.append(
                f"{line}: CS-P15: 파일 단위 `# ruff: noqa`를 쓰지 않는다. "
                f"필요한 줄에만 규칙 코드를 지정한다. {CLOCK_HINT}"
            )
            continue
        for match in NOQA.finditer(token.string):
            codes_match = NOQA_CODES.match(token.string, match.end())
            if codes_match is None:
                errors.append(
                    f"{line}: CS-P15: 규칙을 지정하지 않은 `# noqa`를 쓰지 않는다. "
                    "`# noqa: F401`처럼 규칙 코드를 지정한다"
                )
                continue
            codes = re.split(r"[\s,]+", codes_match.group(1).upper())
            if any(code.startswith(("TID251", "DTZ")) for code in codes):
                errors.append(
                    f"{line}: CS-P15: `noqa`로 TID251·DTZ를 억제하지 않는다. "
                    f"{CLOCK_HINT}"
                )
    return errors


def is_banned_ignore(code: str) -> bool:
    code = code.upper()
    return code in {"ALL", "TID", "TID2", "TID25"} or code.startswith(("TID251", "DTZ"))


def check_ruff_config(pyproject: str) -> list[str]:
    """(d) pyproject.toml의 Ruff 설정에서 TID251·DTZ 무력화와 제외 경로를 찾는다."""
    ruff = tomllib.loads(pyproject).get("tool", {}).get("ruff", {})
    errors = []
    for section_name, section in (
        ("tool.ruff", ruff),
        ("tool.ruff.lint", ruff.get("lint", {})),
    ):
        for key in ("exclude", "extend-exclude"):
            if key in section:
                errors.append(
                    f"pyproject.toml: CS-P15: `{section_name}.{key}`로 검사 대상을 "
                    f"제외하지 않는다. 설정을 삭제한다. {CLOCK_HINT}"
                )
        for key in ("ignore", "extend-ignore"):
            banned = [code for code in section.get(key, []) if is_banned_ignore(code)]
            if banned:
                errors.append(
                    f"pyproject.toml: CS-P15: `{section_name}.{key}`에서 {banned}를 "
                    f"뺀다. {CLOCK_HINT}"
                )
        for key in ("per-file-ignores", "extend-per-file-ignores"):
            for pattern, codes in section.get(key, {}).items():
                banned = [
                    code
                    for code in codes
                    if is_banned_ignore(code)
                    and not (
                        key == "per-file-ignores" and (pattern, code) == CLOCK_EXCEPTION
                    )
                ]
                if banned:
                    errors.append(
                        f"pyproject.toml: CS-P15: `{section_name}.{key}`의 "
                        f"`{pattern}`에서 {banned}를 뺀다. TID251 예외는 "
                        f"`{CLOCK_EXCEPTION[0]}` 하나뿐이다. {CLOCK_HINT}"
                    )
    return errors


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


def python_files(root: Path) -> list[Path]:
    files = []
    for directory in PYTHON_DIRS:
        files.extend(
            path
            for path in (root / directory).rglob("*.py")
            if not SKIPPED_DIRS.intersection(path.relative_to(root).parts)
        )
    return sorted(files)


def ruff_config_files(root: Path) -> list[Path]:
    found = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [
            name for name in subdirectories if name not in SKIPPED_DIRS
        ]
        found.extend(
            Path(directory) / name for name in files if name in RUFF_CONFIG_FILES
        )
    return sorted(found)


def validate(root: Path = ROOT) -> list[str]:
    errors = []
    for path in python_files(root):
        relative = path.relative_to(root)
        source = path.read_text()
        try:
            tree = ast.parse(source)
            file_errors = check_suppression_comments(source) + check_clock_overrides(
                tree
            )
        except (SyntaxError, tokenize.TokenError) as error:
            errors.append(f"{relative}: 코드 분석 실패: {error}")
            continue
        if relative.parts[0] == "tests":
            file_errors += check_test_clock_lambdas(tree)
        if relative.parts[0] == "src":
            file_errors += check_init_clock(tree)
        errors.extend(f"{relative}:{error}" for error in file_errors)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        errors.extend(check_ruff_config(pyproject.read_text()))
    errors.extend(
        f"{path.relative_to(root)}: CS-P15: Ruff 설정은 `pyproject.toml` 하나에 둔다. "
        f"이 파일을 삭제한다. {CLOCK_HINT}"
        for path in ruff_config_files(root)
    )
    return errors


if __name__ == "__main__":
    failures = validate()
    print("\n".join(failures) if failures else "코드 규칙 검사 통과")
    raise SystemExit(bool(failures))

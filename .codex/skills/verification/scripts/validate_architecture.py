"""선언한 import 경계를 AST로 검사한다. 동적 import나 의미적 설계는 판정하지 않는다."""

import ast
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


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


def check_source(source: str, module: str) -> list[str]:
    errors = []
    for line, target in imported_modules(ast.parse(source), module.rpartition(".")[0]):
        if module.rsplit(".", 1)[-1] in {"models", "repository"} and (
            target.split(".")[0] in {"fastapi", "starlette"}
            or (target.startswith("mosemo.") and "router" in target.split("."))
        ):
            errors.append(f"{line}: {target}: 모델·저장소에서 HTTP 계층 참조 금지")
        if module in {"mosemo.exceptions", "mosemo.responses"} and (
            target == "mosemo.openapi" or target.startswith("mosemo.openapi.")
        ):
            errors.append(f"{line}: {target}: ADR 0001 런타임 → OpenAPI 의존 금지")
    return sorted(set(errors))


def validate(root: Path = ROOT) -> list[str]:
    errors = []
    for path in sorted((root / "src" / "mosemo").rglob("*.py")):
        module = ".".join(path.relative_to(root / "src").with_suffix("").parts)
        try:
            errors.extend(
                f"{path.relative_to(root)}:{error}"
                for error in check_source(path.read_text(), module)
            )
        except (SyntaxError, ImportError, ValueError) as error:
            errors.append(f"{path.relative_to(root)}: import 분석 실패: {error}")
    if not (root / "src" / "mosemo" / "main.py").is_file():
        errors.append("애플리케이션 진입점 없음: src/mosemo/main.py")
    return errors


if __name__ == "__main__":
    failures = validate()
    print("\n".join(failures) if failures else "아키텍처 검사 통과")
    raise SystemExit(bool(failures))

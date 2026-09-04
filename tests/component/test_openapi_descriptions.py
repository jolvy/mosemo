import os
from typing import Any

import pytest

HTTP_METHODS = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)
EXCLUDED_OBJECT_SCHEMAS = frozenset({"HTTPValidationError", "ValidationError"})
OPENAPI_TEST_ENVIRONMENT = {
    "MOSEMO_ENV": "openapi",
    "DB_DATABASE": "mosemo_openapi",
    "DB_USER": "mosemo",
    "DB_PASSWORD": "mosemo",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "KAKAO_REST_API_KEY": "openapi-placeholder",
    "KAKAO_CLIENT_SECRET": "openapi-placeholder",
    "KAKAO_REDIRECT_URI": "http://localhost:8000/api/v1/auth/kakao/callback",
    "AUTH_JWT_SECRET_KEY": "openapi-placeholder-key-at-least-32-bytes",
    "AUTH_MACOS_CALLBACK_URI": "com.example.mosemo:/auth/callback",
}


def _has_description(value: dict[str, Any]) -> bool:
    description = value.get("description")
    return isinstance(description, str) and bool(description.strip())


def _find_missing_descriptions(document: dict[str, Any]) -> list[str]:
    missing: list[str] = []

    schemas = document.get("components", {}).get("schemas", {})
    for schema_name, schema in schemas.items():
        if schema_name in EXCLUDED_OBJECT_SCHEMAS or schema.get("type") != "object":
            continue

        if not _has_description(schema):
            missing.append(f"schema {schema_name}: description")

        for field_name, field_schema in schema.get("properties", {}).items():
            if not _has_description(field_schema):
                missing.append(f"schema {schema_name}.{field_name}: description")

    paths = document.get("paths", {})
    for path, path_item in paths.items():
        path_parameters = path_item.get("parameters", [])

        for method, operation in path_item.items():
            if method not in HTTP_METHODS:
                continue

            method_name = method.upper()
            if not _has_description(operation):
                missing.append(f"operation {method_name} {path}: description")

            parameters = [
                *path_parameters,
                *operation.get("parameters", []),
            ]
            for parameter in parameters:
                if not _has_description(parameter):
                    location = parameter.get("in", "unknown")
                    name = parameter.get("name", "unknown")
                    missing.append(
                        f"parameter {method_name} {path} {location} {name}: description"
                    )

            for status_code, response in operation.get("responses", {}).items():
                if not _has_description(response):
                    missing.append(
                        f"response {method_name} {path} {status_code}: description"
                    )

    return sorted(missing)


def test_public_openapi_descriptions_are_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in OPENAPI_TEST_ENVIRONMENT.items():
        if name not in os.environ:
            monkeypatch.setenv(name, value)

    from mosemo.main import app

    missing = _find_missing_descriptions(app.openapi())
    details = "\n".join(f"- {item}" for item in missing)

    assert not missing, f"Missing OpenAPI descriptions:\n{details}"

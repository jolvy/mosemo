import json
import os
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from mosemo.exceptions import ErrorCode, ErrorSpec
from mosemo.openapi import ERROR_DOCS

HTTP_METHODS = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
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
    "AUTH_JWT_ISSUER": "mosemo",
    "AUTH_JWT_AUDIENCE": "mosemo-api",
    "AUTH_ACCESS_TOKEN_TTL_SECONDS": "86400",
    "AUTH_AUTHORIZATION_CODE_TTL_SECONDS": "60",
    "AUTH_MACOS_CALLBACK_URI": "com.example.mosemo:/auth/callback",
}
OPENAPI_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "openapi" / "openapi.json"
API_DOCUMENTATION_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "API_DOCUMENTATION.md"
)
_PUBLIC_ERROR_SPECS = tuple(
    value for name, value in vars(ErrorCode).items() if not name.startswith("_")
)


@pytest.fixture
def openapi_document(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    for name, value in OPENAPI_TEST_ENVIRONMENT.items():
        if name not in os.environ:
            monkeypatch.setenv(name, value)

    from mosemo.main import app

    return app.openapi()


def _has_description(value: dict[str, Any]) -> bool:
    description = value.get("description")
    return isinstance(description, str) and bool(description.strip())


def _iter_operations(
    document: dict[str, Any],
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, path_item in document.get("paths", {}).items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def test_public_openapi_descriptions_are_present(
    openapi_document: dict[str, Any],
) -> None:
    missing: list[str] = []
    for name, schema in openapi_document["components"]["schemas"].items():
        if schema.get("type") != "object":
            continue
        if not _has_description(schema):
            missing.append(f"schema {name}: description")
        for field_name, field_schema in schema.get("properties", {}).items():
            if not _has_description(field_schema):
                missing.append(f"schema {name}.{field_name}: description")
    for path, method, operation in _iter_operations(openapi_document):
        if not _has_description(operation):
            missing.append(f"operation {method.upper()} {path}: description")
        path_parameters = openapi_document["paths"][path].get("parameters", [])
        for parameter in [*path_parameters, *operation.get("parameters", [])]:
            if not _has_description(parameter):
                missing.append(f"parameter {method.upper()} {path} {parameter['name']}")
        for status_code, response in operation.get("responses", {}).items():
            if not _has_description(response):
                missing.append(f"response {method.upper()} {path} {status_code}")
    assert not missing, "Missing OpenAPI descriptions:\n" + "\n".join(missing)


def test_error_code_registry_and_error_docs_have_exact_coverage() -> None:
    assert all(isinstance(spec, ErrorSpec) for spec in _PUBLIC_ERROR_SPECS)
    error_codes = set(_PUBLIC_ERROR_SPECS)
    assert set(ERROR_DOCS) == error_codes
    assert all(
        docs.summary.strip() and docs.description.strip()
        for docs in ERROR_DOCS.values()
    )


def test_path_level_parameters_are_checked_alongside_operation_parameters(
    openapi_document: dict[str, Any],
) -> None:
    path_level_parameters = [
        parameter
        for path_item in openapi_document["paths"].values()
        for parameter in path_item.get("parameters", [])
    ]
    assert all(_has_description(parameter) for parameter in path_level_parameters)


def test_openapi_metadata_and_public_data_contract_are_stable(
    openapi_document: dict[str, Any],
) -> None:
    assert openapi_document["info"]["title"] == "Mosemo API"
    assert openapi_document["info"]["version"] == "0.1.0"
    description = openapi_document["info"]["description"]
    assert "camelCase" in description
    assert "0~6자리" in description
    assert "Python 값에서는 마이크로초를 보존" in description
    assert "YYYY-MM-DDTHH:MM:SSZ" in description
    assert "공통 envelope" in description

    schemas = openapi_document["components"]["schemas"]
    assert set(schemas["TokenRequest"]["properties"]) == {
        "grantType",
        "code",
        "codeVerifier",
    }
    assert set(schemas["AccountResponse"]["properties"]) == {
        "accountId",
        "provider",
        "createdAt",
        "lastAuthenticatedAt",
    }
    assert set(schemas["ActivityCreateResponse"]["properties"]) == {
        "eventId",
        "status",
        "receivedAt",
    }
    created_at_schema = schemas["AccountResponse"]["properties"]["createdAt"]
    assert created_at_schema["type"] == "string"
    assert created_at_schema["format"] == "date-time"
    assert created_at_schema["pattern"].endswith("Z$")


def test_success_responses_document_content_and_important_headers(
    openapi_document: dict[str, Any],
) -> None:
    operations_by_id: dict[str, dict[str, Any]] = {}
    for path, method, operation in _iter_operations(openapi_document):
        operations_by_id[operation["operationId"]] = operation
        for status_code, response in operation["responses"].items():
            numeric_status = int(status_code)
            if not 200 <= numeric_status < 400:
                continue

            if numeric_status >= 300 or numeric_status in {204, 205}:
                assert "content" not in response, (path, method, status_code)
            else:
                content = response.get("content")
                assert isinstance(content, dict) and content, (
                    path,
                    method,
                    status_code,
                )
                assert all("schema" in media for media in content.values()), (
                    path,
                    method,
                    status_code,
                )

            headers = response.get("headers", {})
            assert all(
                _has_description(header) and "schema" in header
                for header in headers.values()
            ), (path, method, status_code)

            if numeric_status in REDIRECT_STATUS_CODES:
                assert "Location" in headers, (path, method, status_code)

    expected_headers = {
        "authKakaoLogin": {"Location", "Set-Cookie", "Cache-Control", "Pragma"},
        "authKakaoCallback": {
            "Location",
            "Set-Cookie",
            "Cache-Control",
            "Pragma",
        },
        "authExchangeToken": {"Cache-Control", "Pragma"},
    }
    for operation_id, header_names in expected_headers.items():
        success_responses = {
            code: response
            for code, response in operations_by_id[operation_id]["responses"].items()
            if 200 <= int(code) < 400
        }
        assert len(success_responses) == 1
        response = next(iter(success_responses.values()))
        assert set(response["headers"]) == header_names


def test_api_documentation_describes_replacement_contract() -> None:
    documentation = API_DOCUMENTATION_PATH.read_text(encoding="utf-8")
    for required_text in (
        '"status": "INVALID_ARGUMENT"',
        "공통 `ErrorResponse` schema",
        "`loc`, `msg`, `type`",
        "invalid_encoding",
        "macOS 생성 클라이언트",
        "문서화되지 않은 `Starlette HTTPException` 4xx·5xx",
        "Swagger UI 수동 검수는 완료 조건에 포함하지 않는다",
    ):
        assert required_text in documentation
    for obsolete_text in (
        "MosemoApiException",
        "REQUEST_VALIDATION_FAILED",
        "FieldViolation",
    ):
        assert obsolete_text not in documentation


def test_openapi_uses_one_common_error_schema_and_framework_validation_detail(
    openapi_document: dict[str, Any],
) -> None:
    schemas = openapi_document["components"]["schemas"]
    assert set(schemas["ErrorResponse"]["properties"]) == {"error"}
    assert schemas["ErrorResponse"]["required"] == ["error"]
    assert schemas["ErrorResponse"]["additionalProperties"] is False
    assert schemas["ErrorPayload"]["required"] == [
        "status",
        "code",
        "message",
        "details",
    ]
    assert schemas["ValidationDetail"]["required"] == [
        "loc",
        "msg",
        "type",
    ]
    assert schemas["ValidationDetail"]["properties"]["type"]["type"] == "string"
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    error_schemas = {
        name: schemas[name]
        for name in ("ErrorResponse", "ErrorPayload", "ValidationDetail")
    }
    assert "oneOf" not in json.dumps(error_schemas)
    assert "discriminator" not in json.dumps(error_schemas)


def test_public_error_examples_match_status_and_common_schema(
    openapi_document: dict[str, Any],
) -> None:
    error_response_ref = {"$ref": "#/components/schemas/ErrorResponse"}
    for path, method, operation in _iter_operations(openapi_document):
        responses = operation["responses"]
        assert {"404", "405", "500"} <= set(responses), (path, method)
        for status_code, response in responses.items():
            if int(status_code) < 400:
                continue
            assert response["description"] == HTTPStatus(int(status_code)).phrase
            assert (
                response["content"]["application/json"]["schema"] == error_response_ref
            )
            for status, example in response["content"]["application/json"][
                "examples"
            ].items():
                error = example["value"]["error"]
                spec = next(
                    spec for spec in _PUBLIC_ERROR_SPECS if spec.status == status
                )
                assert example["summary"] == ERROR_DOCS[spec].summary
                assert example["description"] == ERROR_DOCS[spec].description
                assert error["code"] == int(status_code)
                assert error["status"] == status
                assert error["details"] == (
                    [
                        {
                            "loc": ["body", "users", 0, "email"],
                            "msg": "Field required",
                            "type": "missing",
                        }
                    ]
                    if error["status"] == "INVALID_ARGUMENT"
                    else []
                )

        if path == "/api/v1/accounts/me":
            assert "422" not in responses

    callback_responses = openapi_document["paths"]["/api/v1/auth/kakao/callback"][
        "get"
    ]["responses"]
    assert "422" not in callback_responses
    assert set(
        callback_responses["400"]["content"]["application/json"]["examples"]
    ) == {"AUTH_INVALID_OAUTH_CONTEXT"}

    activity_responses = openapi_document["paths"]["/api/v1/activities"]["post"][
        "responses"
    ]
    assert "201" in activity_responses
    assert set(
        activity_responses["404"]["content"]["application/json"]["examples"]
    ) == {
        "REQUEST_ROUTE_NOT_FOUND",
        "ACTIVITY_DEVICE_NOT_FOUND",
    }
    assert set(
        activity_responses["409"]["content"]["application/json"]["examples"]
    ) == {
        "ACTIVITY_EVENT_ID_CONFLICT",
        "ACTIVITY_SEQUENCE_CONFLICT",
    }


def test_openapi_documents_fixed_and_dynamic_error_headers(
    openapi_document: dict[str, Any],
) -> None:
    operations = openapi_document["paths"]
    auth_headers = operations["/api/v1/accounts/me"]["get"]["responses"]["401"][
        "headers"
    ]
    assert auth_headers["WWW-Authenticate"]["schema"] == {
        "type": "string",
        "const": "Bearer",
    }
    for _path, _method, operation in _iter_operations(openapi_document):
        assert operation["responses"]["405"]["headers"]["Allow"]["schema"] == {
            "type": "string"
        }
    callback_headers = operations["/api/v1/auth/kakao/callback"]["get"]["responses"][
        "400"
    ]["headers"]
    assert {"Set-Cookie", "Cache-Control", "Pragma"} <= set(callback_headers)


def test_openapi_operation_ids_are_unique(openapi_document: dict[str, Any]) -> None:
    operation_ids = [
        operation["operationId"]
        for _path, _method, operation in _iter_operations(openapi_document)
    ]
    assert len(set(operation_ids)) == len(operation_ids)


def test_exported_openapi_matches_runtime_schema(
    openapi_document: dict[str, Any],
) -> None:
    exported_document = json.loads(OPENAPI_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert exported_document == openapi_document, (
        "OpenAPI snapshot is stale. Run `uv run --locked poe openapi`."
    )

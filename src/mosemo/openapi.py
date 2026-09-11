from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from mosemo.exceptions import ErrorCode, ErrorSpec
from mosemo.schemas import ErrorPayload, ErrorResponse, ValidationDetail


@dataclass(frozen=True, slots=True)
class ErrorDocs:
    summary: str
    description: str
    example_details: tuple[ValidationDetail, ...] = ()


ERROR_DOCS: Mapping[ErrorSpec, ErrorDocs] = {
    ErrorCode.AUTH_INVALID_AUTHORIZATION_CODE: ErrorDocs(
        summary="Invalid authorization code",
        description="authorization code 또는 PKCE 검증에 실패했습니다.",
    ),
    ErrorCode.AUTH_INVALID_OAUTH_CONTEXT: ErrorDocs(
        summary="Invalid OAuth context",
        description="OAuth login context가 유효하지 않습니다.",
    ),
    ErrorCode.AUTH_INVALID_ACCESS_TOKEN: ErrorDocs(
        summary="Invalid access token",
        description="Bearer 액세스 토큰이 유효하지 않거나 누락되었습니다.",
    ),
    ErrorCode.REQUEST_ROUTE_NOT_FOUND: ErrorDocs(
        summary="Route not found", description="요청한 API route를 찾을 수 없습니다."
    ),
    ErrorCode.REQUEST_METHOD_NOT_ALLOWED: ErrorDocs(
        summary="Method not allowed",
        description="요청 method가 해당 API route에서 허용되지 않습니다.",
    ),
    ErrorCode.INVALID_ARGUMENT: ErrorDocs(
        summary="Invalid argument",
        description="요청 입력값 검증에 실패했습니다.",
        example_details=(
            ValidationDetail(
                loc=["body", "users", 0, "email"],
                msg="Field required",
                type="missing",
            ),
        ),
    ),
    ErrorCode.INTERNAL_SERVER_ERROR: ErrorDocs(
        summary="Internal server error",
        description="예상하지 못한 서버 오류가 발생했습니다.",
    ),
}

_FIXED_HEADER_METADATA: dict[str, dict[str, Any]] = {
    "www-authenticate": {
        "description": "클라이언트가 사용해야 하는 인증 방식입니다.",
        "schema": {"type": "string", "const": "Bearer"},
    },
}
_DYNAMIC_HEADER_METADATA: dict[str, dict[str, Any]] = {
    "allow": {
        "description": "해당 route에서 허용되는 HTTP method 목록입니다.",
        "schema": {"type": "string"},
    },
}


def _error_example(spec: ErrorSpec) -> dict[str, Any]:
    docs = ERROR_DOCS[spec]
    return ErrorResponse(
        error=ErrorPayload(
            status=spec.status,
            code=spec.code,
            message=spec.message,
            details=list(docs.example_details),
        )
    ).model_dump(mode="json")


def _merge_headers(
    specs: Sequence[ErrorSpec],
    additional_headers: Mapping[int, Mapping[str, Any]] | None,
    status_code: int,
) -> dict[str, dict[str, Any]]:
    headers: dict[str, dict[str, Any]] = {}
    if ErrorCode.AUTH_INVALID_ACCESS_TOKEN in specs:
        headers["WWW-Authenticate"] = _FIXED_HEADER_METADATA["www-authenticate"]
    if ErrorCode.REQUEST_METHOD_NOT_ALLOWED in specs:
        headers["Allow"] = _DYNAMIC_HEADER_METADATA["allow"]

    if additional_headers is not None:
        existing_names = {header.casefold() for header in headers}
        for name, value in additional_headers.get(status_code, {}).items():
            if name.casefold() in existing_names:
                raise ValueError(
                    f"additional header conflicts with documented header: {name}"
                )
            headers[name] = value
            existing_names.add(name.casefold())

    return headers


def api_error_responses(
    *errors: ErrorSpec,
    headers: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[int | str, dict[str, Any]]:
    """Project public ErrorSpec values into FastAPI response declarations."""

    if not errors:
        raise ValueError("at least one ErrorSpec is required")
    if any(not isinstance(error, ErrorSpec) for error in errors):
        raise TypeError("api_error_responses accepts ErrorSpec values")
    if len(set(errors)) != len(errors):
        raise ValueError(
            "api_error_responses does not accept duplicate ErrorSpec values"
        )

    grouped: dict[int, list[ErrorSpec]] = {}
    for error in errors:
        grouped.setdefault(error.code, []).append(error)

    return {
        status_code: {
            "model": ErrorResponse,
            "description": HTTPStatus(status_code).phrase,
            "content": {
                "application/json": {
                    "examples": {
                        spec.status: {
                            "summary": ERROR_DOCS[spec].summary,
                            "description": ERROR_DOCS[spec].description,
                            "value": _error_example(spec),
                        }
                        for spec in specs_for_status
                    }
                }
            },
            "headers": _merge_headers(
                specs_for_status,
                headers,
                status_code,
            ),
        }
        for status_code, specs_for_status in grouped.items()
    }


def public_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema is not None:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            response = operation.get("responses", {}).get("422")
            if (
                isinstance(response, dict)
                and response.get("content", {})
                .get("application/json", {})
                .get("schema", {})
                .get("$ref")
                == "#/components/schemas/HTTPValidationError"
            ):
                del operation["responses"]["422"]

    schemas = schema.get("components", {}).get("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    app.openapi_schema = schema
    return schema

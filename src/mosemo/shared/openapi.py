from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from mosemo.shared.errors import ApiErrorCode, CommonErrorCode
from mosemo.shared.presentation.schemas import (
    ErrorPayload,
    ErrorResponse,
    ValidationDetail,
)


@dataclass(frozen=True, slots=True)
class ErrorDocs:
    summary: str
    description: str
    example_details: tuple[ValidationDetail, ...] = ()


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


def error_example(
    spec: ApiErrorCode,
    details: Sequence[ValidationDetail] = (),
) -> dict[str, Any]:
    return ErrorResponse(
        error=ErrorPayload(
            status=spec.status,
            code=spec.code,
            message=spec.message,
            details=list(details),
        )
    ).model_dump(mode="json")


def _merge_headers(
    specs: Sequence[ApiErrorCode],
    additional_headers: Mapping[int, Mapping[str, Any]] | None,
    *,
    status_code: int,
) -> dict[str, dict[str, Any]]:
    headers: dict[str, dict[str, Any]] = {}
    if CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN in specs:
        headers["WWW-Authenticate"] = _FIXED_HEADER_METADATA["www-authenticate"]
    if CommonErrorCode.REQUEST_METHOD_NOT_ALLOWED in specs:
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
    *errors: ApiErrorCode,
    headers: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[int | str, dict[str, Any]]:
    """Declare FastAPI error responses grouped by HTTP status.

    Example summaries, descriptions and details are filled by the assembly root's
    ``public_openapi()`` from the registered ``ERROR_DOCS``.
    """

    if not errors:
        raise ValueError("at least one ApiErrorCode is required")
    if any(not isinstance(error, ApiErrorCode) for error in errors):
        raise TypeError("api_error_responses accepts ApiErrorCode members")
    if len(set(errors)) != len(errors):
        raise ValueError(
            "api_error_responses does not accept duplicate ApiErrorCode members"
        )

    grouped: dict[int, list[ApiErrorCode]] = {}
    for error in errors:
        grouped.setdefault(error.code, []).append(error)

    return {
        status_code: {
            "model": ErrorResponse,
            "description": HTTPStatus(status_code).phrase,
            "content": {
                "application/json": {
                    "examples": {
                        spec.status: {"value": error_example(spec)}
                        for spec in specs_for_status
                    }
                }
            },
            "headers": _merge_headers(
                specs_for_status,
                headers,
                status_code=status_code,
            ),
        }
        for status_code, specs_for_status in grouped.items()
    }

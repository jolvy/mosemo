from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from mosemo.api.error_docs import ERROR_DOCS as CENTRAL_ERROR_DOCS
from mosemo.exceptions import ErrorCode
from mosemo.shared.error_docs import ERROR_DOCS as COMMON_ERROR_DOCS
from mosemo.shared.errors import ApiErrorCode, CommonErrorCode
from mosemo.shared.openapi import ErrorDocs, error_example

# Every public error code enum and the ERROR_DOCS that documents it.
REGISTERED_ERROR_DOCS: Mapping[type[ApiErrorCode], Mapping[ApiErrorCode, ErrorDocs]] = {
    CommonErrorCode: COMMON_ERROR_DOCS,
    ErrorCode: CENTRAL_ERROR_DOCS,
}

_ERROR_RESPONSE_REF = "#/components/schemas/ErrorResponse"
_VALIDATION_ERROR_REF = "#/components/schemas/HTTPValidationError"


def merge_error_docs(
    registered: Iterable[Mapping[ApiErrorCode, ErrorDocs]],
) -> dict[str, tuple[ApiErrorCode, ErrorDocs]]:
    merged: dict[str, tuple[ApiErrorCode, ErrorDocs]] = {}
    for error_docs in registered:
        for code, docs in error_docs.items():
            if code.status in merged:
                raise ValueError(f"duplicate error status in ERROR_DOCS: {code.status}")
            merged[code.status] = (code, docs)
    return merged


def _operations(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                yield operation


def _json_content(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    return response.get("content", {}).get("application/json", {})


def _documented_examples(
    examples: Mapping[str, Any],
    docs_by_status: Mapping[str, tuple[ApiErrorCode, ErrorDocs]],
) -> dict[str, dict[str, Any]]:
    documented: dict[str, dict[str, Any]] = {}
    for status in examples:
        if status not in docs_by_status:
            raise ValueError(f"no ERROR_DOCS registered for error status: {status}")
        code, docs = docs_by_status[status]
        documented[status] = {
            "summary": docs.summary,
            "description": docs.description,
            "value": error_example(code, docs.example_details),
        }
    return documented


def fill_error_examples(
    schema: dict[str, Any],
    docs_by_status: Mapping[str, tuple[ApiErrorCode, ErrorDocs]],
) -> None:
    for operation in _operations(schema):
        for response in operation.get("responses", {}).values():
            content = _json_content(response)
            if content.get("schema", {}).get("$ref") != _ERROR_RESPONSE_REF:
                continue
            content["examples"] = _documented_examples(
                content.get("examples", {}), docs_by_status
            )


def _remove_framework_validation_responses(schema: dict[str, Any]) -> None:
    for operation in _operations(schema):
        response = operation.get("responses", {}).get("422")
        if _json_content(response).get("schema", {}).get("$ref") == (
            _VALIDATION_ERROR_REF
        ):
            del operation["responses"]["422"]

    schemas = schema.get("components", {}).get("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)


def public_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema is not None:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    _remove_framework_validation_responses(schema)
    fill_error_examples(schema, merge_error_docs(REGISTERED_ERROR_DOCS.values()))
    app.openapi_schema = schema
    return schema

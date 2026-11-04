from typing import Any

import pytest

from mosemo.api.openapi import (
    REGISTERED_ERROR_DOCS,
    fill_error_examples,
    merge_error_docs,
)
from mosemo.exceptions import ErrorCode
from mosemo.identity.public import IdentityErrorCode
from mosemo.shared.error_docs import ERROR_DOCS as COMMON_ERROR_DOCS
from mosemo.shared.errors import ApiErrorCode, CommonErrorCode
from mosemo.shared.openapi import ErrorDocs

# ADR 0014 「오류 코드 이름」: enum별 허용 접두사와 접두사 밖에서 허용하는 멤버 이름.
ALLOWED_NAMES: dict[type[ApiErrorCode], tuple[tuple[str, ...], frozenset[str]]] = {
    CommonErrorCode: (
        ("REQUEST_", "INVALID_", "INTERNAL_"),
        frozenset({"AUTH_INVALID_ACCESS_TOKEN"}),
    ),
    IdentityErrorCode: (("AUTH_",), frozenset()),
    # Temporary central enum: union of the BC prefixes until each BC moves out.
    ErrorCode: (
        ("LABEL_", "ACTIVITY_", "FOCUS_SESSION_", "LABELING_"),
        frozenset(),
    ),
}


def test_every_error_code_enum_is_registered_with_a_prefix_rule() -> None:
    assert set(ApiErrorCode.__subclasses__()) == set(REGISTERED_ERROR_DOCS)
    assert set(REGISTERED_ERROR_DOCS) == set(ALLOWED_NAMES)


def test_error_statuses_are_unique_across_registered_enums() -> None:
    statuses = [member.status for enum in REGISTERED_ERROR_DOCS for member in enum]
    assert len(statuses) == len(set(statuses))


@pytest.mark.parametrize("enum", list(ALLOWED_NAMES))
def test_error_statuses_use_the_enum_allowed_prefixes(
    enum: type[ApiErrorCode],
) -> None:
    prefixes, exact_names = ALLOWED_NAMES[enum]
    assert [
        member.status
        for member in enum
        if member.status not in exact_names and not member.status.startswith(prefixes)
    ] == []


@pytest.mark.parametrize(("enum", "error_docs"), list(REGISTERED_ERROR_DOCS.items()))
def test_registered_error_docs_match_their_enum_exactly(
    enum: type[ApiErrorCode], error_docs: dict[ApiErrorCode, ErrorDocs]
) -> None:
    assert all(isinstance(code, enum) for code in error_docs)
    assert set(error_docs) == set(enum)
    assert all(
        docs.summary.strip() and docs.description.strip()
        for docs in error_docs.values()
    )


def test_merge_error_docs_rejects_duplicate_statuses() -> None:
    with pytest.raises(ValueError, match="duplicate error status"):
        merge_error_docs([COMMON_ERROR_DOCS, COMMON_ERROR_DOCS])


def _schema(responses: dict[str, Any]) -> dict[str, Any]:
    return {"paths": {"/x": {"parameters": [], "get": {"responses": responses}}}}


def _error_response(*statuses: str) -> dict[str, Any]:
    return {
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                "examples": {status: {"value": {}} for status in statuses},
            }
        }
    }


def test_fill_error_examples_uses_registered_docs() -> None:
    other = {"content": {"application/json": {"examples": {"raw": {"value": 1}}}}}
    schema = _schema({"422": _error_response("INVALID_ARGUMENT"), "200": other})

    fill_error_examples(schema, merge_error_docs(REGISTERED_ERROR_DOCS.values()))

    responses = schema["paths"]["/x"]["get"]["responses"]
    docs = COMMON_ERROR_DOCS[CommonErrorCode.INVALID_ARGUMENT]
    example = responses["422"]["content"]["application/json"]["examples"][
        "INVALID_ARGUMENT"
    ]
    assert example["summary"] == docs.summary
    assert example["description"] == docs.description
    assert example["value"]["error"]["details"] == [
        detail.model_dump(mode="json") for detail in docs.example_details
    ]
    assert responses["200"] == other


def test_fill_error_examples_rejects_undocumented_statuses() -> None:
    schema = _schema({"404": _error_response("ACTIVITY_DEVICE_NOT_FOUND")})

    with pytest.raises(ValueError, match="ACTIVITY_DEVICE_NOT_FOUND"):
        fill_error_examples(schema, merge_error_docs([COMMON_ERROR_DOCS]))

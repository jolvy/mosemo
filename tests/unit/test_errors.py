from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from mosemo.exceptions import ApiException, ErrorCode, ErrorSpec
from mosemo.schemas import ValidationDetail


def test_public_error_registry_contains_the_public_specs() -> None:
    public_members = tuple(ErrorCode)
    assert all(isinstance(member.value, ErrorSpec) for member in public_members)
    assert [
        (member.status, member.code, member.message) for member in public_members
    ] == [
        (
            "AUTH_INVALID_AUTHORIZATION_CODE",
            400,
            "Invalid or expired authorization code",
        ),
        (
            "AUTH_INVALID_OAUTH_CONTEXT",
            400,
            "Invalid or expired OAuth login context",
        ),
        ("AUTH_INVALID_ACCESS_TOKEN", 401, "Invalid or expired access token"),
        (
            "ACTIVITY_DEVICE_NOT_FOUND",
            404,
            "Activity device not found",
        ),
        (
            "ACTIVITY_EVENT_ID_CONFLICT",
            409,
            "Activity event ID conflicts with a stored record",
        ),
        (
            "ACTIVITY_SEQUENCE_CONFLICT",
            409,
            "Activity sequence conflicts with a stored record",
        ),
        ("ACTIVITY_TIMELINE_BUSY", 503, "Activity timeline is busy"),
        ("ACTIVITY_SEGMENT_NOT_FOUND", 404, "Activity segment not found"),
        (
            "ACTIVITY_SEGMENT_NOT_LABELABLE",
            409,
            "Activity segment is not labelable",
        ),
        (
            "ACTIVITY_SEGMENT_CHANGED",
            409,
            "Activity segment changed; refresh before confirming",
        ),
        ("LABEL_NOT_AVAILABLE", 404, "Label not found or inactive"),
        ("REQUEST_ROUTE_NOT_FOUND", 404, "API route not found"),
        ("REQUEST_METHOD_NOT_ALLOWED", 405, "Method not allowed"),
        ("INVALID_ARGUMENT", 422, "Request validation failed."),
        ("INTERNAL_SERVER_ERROR", 500, "Internal server error"),
    ]
    assert len({member.status for member in public_members}) == 15


def test_error_spec_is_immutable_and_contains_no_documentation_metadata() -> None:
    with pytest.raises(FrozenInstanceError):
        cast(Any, ErrorCode.AUTH_INVALID_ACCESS_TOKEN.value).code = 400

    assert not hasattr(ErrorCode.AUTH_INVALID_ACCESS_TOKEN.value, "status")
    assert not hasattr(ErrorCode.AUTH_INVALID_ACCESS_TOKEN.value, "description")
    assert not hasattr(ErrorCode.AUTH_INVALID_ACCESS_TOKEN.value, "headers")
    assert not hasattr(ErrorCode.AUTH_INVALID_ACCESS_TOKEN.value, "example")


def test_error_code_members_have_distinct_error_specs() -> None:
    members = tuple(ErrorCode)
    first = ErrorSpec(code=400, message="same")
    second = ErrorSpec(code=400, message="same")

    assert len({id(member.value) for member in members}) == len(members)
    assert first != second


def test_api_exception_accepts_error_codes_and_validation_details() -> None:
    detail = ValidationDetail(
        loc=["body", "email"],
        msg="Field required",
        type="missing",
    )
    error = ApiException(ErrorCode.INVALID_ARGUMENT, details=[detail])

    assert error.spec is ErrorCode.INVALID_ARGUMENT
    assert error.details == (detail,)
    assert str(error) == ErrorCode.INVALID_ARGUMENT.message

    non_validation_error = ApiException(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN, details=[detail]
    )
    assert non_validation_error.details == (detail,)


def test_error_codes_are_enum_members_with_error_specs_as_values() -> None:
    assert isinstance(ErrorCode.AUTH_INVALID_AUTHORIZATION_CODE, ErrorCode)
    assert isinstance(ErrorCode.AUTH_INVALID_OAUTH_CONTEXT, ErrorCode)
    assert isinstance(ErrorCode.INTERNAL_SERVER_ERROR, ErrorCode)

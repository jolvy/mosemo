from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from mosemo.exceptions import ApiException, ErrorCode, ErrorSpec
from mosemo.schemas import ValidationDetail


def test_public_error_registry_contains_the_public_specs() -> None:
    public_members = tuple(
        value for name, value in vars(ErrorCode).items() if not name.startswith("_")
    )
    assert all(isinstance(value, ErrorSpec) for value in public_members)
    assert [(spec.status, spec.code, spec.message) for spec in public_members] == [
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
        ("REQUEST_ROUTE_NOT_FOUND", 404, "API route not found"),
        ("REQUEST_METHOD_NOT_ALLOWED", 405, "Method not allowed"),
        ("INVALID_ARGUMENT", 422, "Request validation failed."),
        ("INTERNAL_SERVER_ERROR", 500, "Internal server error"),
    ]
    assert len({spec.status for spec in public_members}) == 11


def test_error_spec_is_immutable_and_contains_no_documentation_metadata() -> None:
    with pytest.raises(FrozenInstanceError):
        cast(Any, ErrorCode.AUTH_INVALID_ACCESS_TOKEN).code = 400

    assert not hasattr(ErrorCode.AUTH_INVALID_ACCESS_TOKEN, "description")
    assert not hasattr(ErrorCode.AUTH_INVALID_ACCESS_TOKEN, "headers")
    assert not hasattr(ErrorCode.AUTH_INVALID_ACCESS_TOKEN, "example")


def test_api_exception_accepts_error_specs_and_validation_details() -> None:
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


def test_error_specs_are_values_not_exception_subclasses() -> None:
    assert isinstance(ErrorCode.AUTH_INVALID_AUTHORIZATION_CODE, ErrorSpec)
    assert isinstance(ErrorCode.AUTH_INVALID_OAUTH_CONTEXT, ErrorSpec)
    assert isinstance(ErrorCode.INTERNAL_SERVER_ERROR, ErrorSpec)

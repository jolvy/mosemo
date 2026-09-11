from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from mosemo.auth.schemas import TokenRequest
from mosemo.schemas import (
    OBSERVATION_TIMESTAMP_PATTERN,
    PUBLIC_TIMESTAMP_PATTERN,
    ApiRequestModel,
    ApiResponseModel,
    ErrorResponse,
    ObservationTimestamp,
    PublicTimestamp,
    ValidationDetail,
)


class ExampleRequest(ApiRequestModel):
    required_value: str
    optional_value: str | None = None


class ExampleResponse(ApiResponseModel):
    account_id: UUID


class ObservationRequest(ApiRequestModel):
    observed_at: ObservationTimestamp


class PublicResponse(ApiResponseModel):
    occurred_at: PublicTimestamp


def test_api_base_models_have_no_datetime_serializer() -> None:
    assert not ApiRequestModel.__pydantic_decorators__.field_serializers
    assert not ApiResponseModel.__pydantic_decorators__.field_serializers


def test_error_response_has_the_replacement_contract() -> None:
    value = ErrorResponse(
        error={
            "status": "INVALID_ARGUMENT",
            "code": 422,
            "message": "Request validation failed.",
            "details": [
                {
                    "loc": ["body", "users", 0, "email"],
                    "msg": "Field required",
                    "type": "missing",
                }
            ],
        }
    )

    assert value.model_dump(mode="json") == {
        "error": {
            "status": "INVALID_ARGUMENT",
            "code": 422,
            "message": "Request validation failed.",
            "details": [
                {
                    "loc": ["body", "users", 0, "email"],
                    "msg": "Field required",
                    "type": "missing",
                }
            ],
        }
    }


def test_validation_detail_contains_only_framework_loc_msg_and_type() -> None:
    with pytest.raises(ValidationError):
        ValidationDetail.model_validate(
            {
                "loc": ["body", "email"],
                "msg": "Field required",
                "type": "missing",
                "input": "secret",
            }
        )

    with pytest.raises(ValidationError):
        ValidationDetail.model_validate(
            {"location": "body", "field": "email", "reason": "missing"}
        )

    assert ValidationDetail(
        loc=["body", "email"],
        msg="Field required",
        type="missing",
    ).model_dump(mode="json") == {
        "loc": ["body", "email"],
        "msg": "Field required",
        "type": "missing",
    }


def test_error_response_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate(
            {
                "error": {
                    "status": "INVALID_ARGUMENT",
                    "code": 422,
                    "message": "Request validation failed.",
                    "details": [],
                    "reason": "old-contract",
                }
            }
        )


def test_request_accepts_camel_case_and_rejects_snake_case_or_extra_fields() -> None:
    value = ExampleRequest.model_validate_json(
        '{"requiredValue":"required","optionalValue":"optional"}'
    )

    assert value.required_value == "required"
    assert value.optional_value == "optional"

    for payload in (
        {"required_value": "required"},
        {"requiredValue": "required", "optional_value": "optional"},
        {"requiredValue": "required", "extraValue": "unexpected"},
    ):
        with pytest.raises(ValidationError):
            ExampleRequest.model_validate(payload)


def test_response_accepts_python_snake_case_and_serializes_camel_case() -> None:
    value = ExampleResponse(account_id=UUID("01991a54-25c0-7000-8000-000000000001"))

    assert value.model_dump(mode="json") == {
        "accountId": "01991a54-25c0-7000-8000-000000000001"
    }


def test_token_request_uses_camel_case_wire_fields() -> None:
    value = TokenRequest.model_validate(
        {
            "grantType": "authorization_code",
            "code": "one-time-code",
            "codeVerifier": "A" * 43,
        }
    )

    assert value.grant_type == "authorization_code"
    assert value.code_verifier == "A" * 43

    for payload in (
        {
            "grant_type": "authorization_code",
            "code": "one-time-code",
            "codeVerifier": "A" * 43,
        },
        {
            "grantType": "authorization_code",
            "code": "one-time-code",
            "code_verifier": "A" * 43,
        },
    ):
        with pytest.raises(ValidationError):
            TokenRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("2026-09-05T01:02:03Z", datetime(2026, 9, 5, 1, 2, 3, tzinfo=UTC)),
        (
            "2026-09-05T01:02:03.123Z",
            datetime(2026, 9, 5, 1, 2, 3, 123_000, tzinfo=UTC),
        ),
        (
            "2026-09-05T01:02:03.123456Z",
            datetime(2026, 9, 5, 1, 2, 3, 123456, tzinfo=UTC),
        ),
    ],
)
def test_observation_timestamp_accepts_utc_z_strings_and_preserves_microseconds(
    raw_value: str,
    expected: datetime,
) -> None:
    value = ObservationRequest.model_validate({"observedAt": raw_value})
    assert value.observed_at == expected


@pytest.mark.parametrize(
    "raw_value",
    [
        "2026-09-05T01:02:03.1234567Z",
        "2026-09-05T01:02:03+00:00",
        "2026-09-05T01:02:03.123456+09:00",
        1_757_032_923,
        datetime.fromisoformat("2026-09-05T01:02:03"),
    ],
)
def test_observation_timestamp_rejects_non_contract_values(raw_value: object) -> None:
    with pytest.raises(ValidationError):
        ObservationRequest.model_validate({"observedAt": raw_value})


def test_observation_timestamp_schema_is_string_with_agreed_pattern() -> None:
    timestamp_schema = ObservationRequest.model_json_schema(mode="validation")[
        "properties"
    ]["observedAt"]
    assert timestamp_schema["type"] == "string"
    assert timestamp_schema["format"] == "date-time"
    assert timestamp_schema["pattern"] == OBSERVATION_TIMESTAMP_PATTERN


def test_public_timestamp_normalizes_python_value_and_truncates_json_value() -> None:
    value = PublicResponse(
        occurred_at=datetime(
            2026,
            9,
            5,
            10,
            2,
            3,
            987654,
            tzinfo=timezone(timedelta(hours=9)),
        )
    )

    assert value.occurred_at == datetime(2026, 9, 5, 1, 2, 3, 987654, tzinfo=UTC)
    assert value.model_dump()["occurredAt"] == value.occurred_at
    assert value.model_dump(mode="json") == {"occurredAt": "2026-09-05T01:02:03Z"}


def test_public_timestamp_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        PublicResponse(occurred_at=datetime.fromisoformat("2026-09-05T01:02:03"))


def test_public_timestamp_serialization_schema_is_whole_second_utc() -> None:
    timestamp_schema = PublicResponse.model_json_schema(mode="serialization")[
        "properties"
    ]["occurredAt"]
    assert timestamp_schema["type"] == "string"
    assert timestamp_schema["format"] == "date-time"
    assert timestamp_schema["pattern"] == PUBLIC_TIMESTAMP_PATTERN

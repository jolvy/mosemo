import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

from mosemo.exceptions import (
    ApiException,
    ErrorCode,
)
from mosemo.openapi import ERROR_DOCS, api_error_responses
from mosemo.responses import api_exception_response, error_response
from mosemo.schemas import ErrorResponse, ValidationDetail


def test_error_response_uses_one_spec_for_http_and_body_code() -> None:
    response = error_response(ErrorCode.AUTH_INVALID_ACCESS_TOKEN)

    assert response.status_code == 401
    assert json.loads(bytes(response.body)) == {
        "error": {
            "status": "AUTH_INVALID_ACCESS_TOKEN",
            "code": 401,
            "message": "Invalid or expired access token",
            "details": [],
        }
    }


def test_api_exception_response_serializes_validation_details() -> None:
    detail = ValidationDetail(
        loc=["body", "users", 0, "email"],
        msg="Field required",
        type="missing",
    )
    response = api_exception_response(
        ApiException(ErrorCode.INVALID_ARGUMENT, details=[detail])
    )

    assert response.status_code == 422
    assert json.loads(bytes(response.body))["error"]["details"] == [
        {
            "loc": ["body", "users", 0, "email"],
            "msg": "Field required",
            "type": "missing",
        }
    ]


def test_response_builder_serializes_details() -> None:
    response = error_response(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        details=[
            ValidationDetail(
                loc=["body"],
                msg="Field required",
                type="missing",
            )
        ],
    )
    assert json.loads(bytes(response.body))["error"]["details"] == [
        {"loc": ["body"], "msg": "Field required", "type": "missing"}
    ]


def test_api_error_responses_groups_same_status_with_common_schema() -> None:
    responses = api_error_responses(
        ErrorCode.AUTH_INVALID_AUTHORIZATION_CODE,
        ErrorCode.AUTH_INVALID_OAUTH_CONTEXT,
    )

    assert set(responses) == {400}
    response = responses[400]
    assert response["model"] is ErrorResponse
    examples = response["content"]["application/json"]["examples"]
    assert set(examples) == {
        "AUTH_INVALID_AUTHORIZATION_CODE",
        "AUTH_INVALID_OAUTH_CONTEXT",
    }
    assert {
        example["value"]["error"]["status"] for example in examples.values()
    } == set(examples)
    assert response["description"] == "Bad Request"
    for spec in (
        ErrorCode.AUTH_INVALID_AUTHORIZATION_CODE,
        ErrorCode.AUTH_INVALID_OAUTH_CONTEXT,
    ):
        assert examples[spec.status]["summary"] == ERROR_DOCS[spec].summary
        assert examples[spec.status]["description"] == ERROR_DOCS[spec].description


def test_api_error_responses_rejects_invalid_inputs_and_duplicates() -> None:
    with pytest.raises(ValueError, match="at least one"):
        api_error_responses()
    with pytest.raises(TypeError, match="ErrorSpec"):
        api_error_responses(cast(Any, "INVALID_ARGUMENT"))
    with pytest.raises(ValueError, match="duplicate"):
        api_error_responses(ErrorCode.INVALID_ARGUMENT, ErrorCode.INVALID_ARGUMENT)


def test_api_error_responses_documents_fixed_and_dynamic_headers() -> None:
    auth_response = api_error_responses(ErrorCode.AUTH_INVALID_ACCESS_TOKEN)[401]
    assert auth_response["headers"]["WWW-Authenticate"]["schema"] == {
        "type": "string",
        "const": "Bearer",
    }

    method_response = api_error_responses(ErrorCode.REQUEST_METHOD_NOT_ALLOWED)[405]
    assert method_response["headers"]["Allow"]["schema"] == {"type": "string"}


def test_api_error_responses_has_open_validation_type() -> None:
    response = api_error_responses(ErrorCode.INVALID_ARGUMENT)[422]
    example = response["content"]["application/json"]["examples"]["INVALID_ARGUMENT"][
        "value"
    ]
    assert ErrorResponse.model_validate(example).error.details
    detail_schema = ErrorResponse.model_json_schema()["$defs"]["ValidationDetail"]
    assert detail_schema["properties"]["type"]["type"] == "string"
    assert set(detail_schema["properties"]) == {"loc", "msg", "type"}

    with pytest.raises(ValidationError):
        ErrorResponse.model_validate(
            {
                "error": {
                    "status": "INVALID_ARGUMENT",
                    "code": 422,
                    "message": "Request validation failed.",
                    "details": [{"loc": ["body"], "msg": "Field required"}],
                }
            }
        )

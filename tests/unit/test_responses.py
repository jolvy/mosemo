import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

from mosemo.identity.public import IdentityErrorCode
from mosemo.shared.errors import CommonErrorCode
from mosemo.shared.openapi import api_error_responses
from mosemo.shared.presentation.responses import error_response
from mosemo.shared.presentation.schemas import ErrorResponse, ValidationDetail


def test_error_response_uses_one_spec_for_http_and_body_code() -> None:
    response = error_response(CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN)

    assert response.status_code == 401
    assert json.loads(bytes(response.body)) == {
        "error": {
            "status": "AUTH_INVALID_ACCESS_TOKEN",
            "code": 401,
            "message": "Invalid or expired access token",
            "details": [],
        }
    }


def test_response_builder_serializes_details() -> None:
    response = error_response(
        CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
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
        IdentityErrorCode.AUTH_INVALID_AUTHORIZATION_CODE,
        IdentityErrorCode.AUTH_INVALID_OAUTH_CONTEXT,
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
    # Summaries, descriptions and details are filled by public_openapi().
    assert all(set(example) == {"value"} for example in examples.values())
    assert all(
        example["value"]["error"]["details"] == [] for example in examples.values()
    )


def test_api_error_responses_rejects_invalid_inputs_and_duplicates() -> None:
    with pytest.raises(ValueError, match="at least one"):
        api_error_responses()
    with pytest.raises(TypeError, match="ApiErrorCode"):
        api_error_responses(cast(Any, "INVALID_ARGUMENT"))
    with pytest.raises(ValueError, match="duplicate"):
        api_error_responses(
            CommonErrorCode.INVALID_ARGUMENT, CommonErrorCode.INVALID_ARGUMENT
        )


def test_api_error_responses_rejects_headers_that_replace_documented_ones() -> None:
    with pytest.raises(ValueError, match="conflicts with documented header: allow"):
        api_error_responses(
            CommonErrorCode.REQUEST_METHOD_NOT_ALLOWED,
            headers={405: {"allow": {"schema": {"type": "string"}}}},
        )


def test_api_error_responses_documents_fixed_and_dynamic_headers() -> None:
    auth_response = api_error_responses(CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN)[401]
    assert auth_response["headers"]["WWW-Authenticate"]["schema"] == {
        "type": "string",
        "const": "Bearer",
    }

    method_response = api_error_responses(CommonErrorCode.REQUEST_METHOD_NOT_ALLOWED)[
        405
    ]
    assert method_response["headers"]["Allow"]["schema"] == {"type": "string"}


def test_api_error_responses_has_open_validation_type() -> None:
    response = api_error_responses(CommonErrorCode.INVALID_ARGUMENT)[422]
    example = response["content"]["application/json"]["examples"]["INVALID_ARGUMENT"][
        "value"
    ]
    assert ErrorResponse.model_validate(example).error.status == "INVALID_ARGUMENT"
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

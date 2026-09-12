import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from mosemo.exception_handlers import _validation_details, register_exception_handlers
from mosemo.exceptions import (
    ApiException,
    ErrorCode,
)
from mosemo.schemas import ValidationDetail


def test_validation_details_copies_only_loc_msg_and_type() -> None:
    errors: list[dict[str, Any]] = [
        {
            "type": "greater_than_equal",
            "loc": ("body", "users", 0, "email"),
            "msg": "Input should be greater than or equal to 1",
            "input": "secret input",
            "ctx": {"ge": 1},
            "url": "https://errors.pydantic.dev/",
        }
    ]

    assert [
        detail.model_dump(mode="json")
        for detail in _validation_details(RequestValidationError(errors))
    ] == [
        {
            "loc": ["body", "users", 0, "email"],
            "msg": "Input should be greater than or equal to 1",
            "type": "greater_than_equal",
        }
    ]


def test_validation_details_preserves_framework_values_and_order() -> None:
    errors: list[dict[str, Any]] = [
        {
            "type": "int_parsing",
            "loc": ("body", "value", "int"),
            "msg": "Input should be a valid integer",
        },
        {
            "type": "missing",
            "loc": ("body", "payload", "ModelA", "count"),
            "msg": "Field required",
        },
        {
            "type": "missing",
            "loc": ("query", "item_id"),
            "msg": "Field required",
        },
    ]

    assert [
        detail.model_dump(mode="json")
        for detail in _validation_details(RequestValidationError(errors))
    ] == [
        {
            "loc": ["body", "value", "int"],
            "msg": "Input should be a valid integer",
            "type": "int_parsing",
        },
        {
            "loc": ["body", "payload", "ModelA", "count"],
            "msg": "Field required",
            "type": "missing",
        },
        {
            "loc": ["query", "item_id"],
            "msg": "Field required",
            "type": "missing",
        },
    ]


@pytest.mark.parametrize("missing_key", ["loc", "msg", "type"])
def test_validation_details_rejects_framework_errors_without_required_keys(
    missing_key: str,
) -> None:
    error: dict[str, Any] = {
        "loc": ("body", "value"),
        "msg": "Field required",
        "type": "missing",
    }
    del error[missing_key]

    with pytest.raises(KeyError, match=missing_key):
        _validation_details(RequestValidationError([error]))


def test_validation_details_rejects_empty_framework_errors() -> None:
    with pytest.raises(ValueError, match="at least one error"):
        _validation_details(RequestValidationError([]))


def test_api_exception_handler_returns_replacement_envelope_and_challenge() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/error")
    def error() -> None:
        raise ApiException(
            ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
            details=[
                ValidationDetail(
                    loc=["header", "Authorization"],
                    msg="Invalid token",
                    type="invalid_token",
                )
            ],
        )

    with TestClient(app) as client:
        response = client.get("/error")

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "status": "AUTH_INVALID_ACCESS_TOKEN",
            "code": 401,
            "message": "Invalid or expired access token",
            "details": [
                {
                    "loc": ["header", "Authorization"],
                    "msg": "Invalid token",
                    "type": "invalid_token",
                }
            ],
        }
    }
    assert response.headers["www-authenticate"] == "Bearer"


def test_framework_not_found_handler_returns_public_route_error() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    with TestClient(app) as client:
        response = client.get("/private/route-not-found")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "status": "REQUEST_ROUTE_NOT_FOUND",
            "code": 404,
            "message": "API route not found",
            "details": [],
        }
    }


def test_framework_method_not_allowed_handler_preserves_allow_header() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/known-route")
    def known_route() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post("/known-route")

    assert response.status_code == 405
    assert response.json()["error"] == {
        "status": "REQUEST_METHOD_NOT_ALLOWED",
        "code": 405,
        "message": "Method not allowed",
        "details": [],
    }
    assert response.headers["allow"] == "GET"


@pytest.mark.parametrize("status_code", [400, 401, 418, 503, 599])
def test_unregistered_http_error_becomes_internal_server_error_and_is_logged(
    status_code: int,
    caplog,
) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/teapot")
    def teapot() -> None:
        raise StarletteHTTPException(
            status_code=status_code,
            detail="secret framework detail",
            headers={"X-Internal-Reason": "secret framework header"},
        )

    caplog.set_level(logging.ERROR, logger="mosemo.exception_handlers")
    exception_logger = logging.getLogger("mosemo.exception_handlers")
    was_disabled = exception_logger.disabled
    exception_logger.disabled = False

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/teapot")
    finally:
        exception_logger.disabled = was_disabled

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "status": "INTERNAL_SERVER_ERROR",
            "code": 500,
            "message": "Internal server error",
            "details": [],
        }
    }
    assert "secret framework detail" not in response.text
    assert "x-internal-reason" not in response.headers
    records = [
        record
        for record in caplog.records
        if record.name == "mosemo.exception_handlers"
    ]
    assert len(records) == 1
    assert records[0].getMessage() == "Unhandled API exception"
    assert records[0].exc_info is not None


def test_non_route_no_body_http_exception_uses_fastapi_default_handler() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/no-content")
    def no_content() -> None:
        raise StarletteHTTPException(status_code=204)

    with TestClient(app) as client:
        response = client.get("/no-content")

    assert response.status_code == 204
    assert response.content == b""


def test_malformed_request_validation_error_becomes_internal_server_error() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/malformed-validation")
    def malformed_validation() -> None:
        raise RequestValidationError([{"loc": ("body", "value")}])

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/malformed-validation")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "status": "INTERNAL_SERVER_ERROR",
            "code": 500,
            "message": "Internal server error",
            "details": [],
        }
    }


def test_unexpected_exception_returns_generic_error_and_logs_original(caplog) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/unexpected")
    def unexpected() -> None:
        raise RuntimeError("secret SQL statement and request data")

    caplog.set_level(logging.ERROR, logger="mosemo.exception_handlers")
    exception_logger = logging.getLogger("mosemo.exception_handlers")
    was_disabled = exception_logger.disabled
    exception_logger.disabled = False

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/unexpected")
    finally:
        exception_logger.disabled = was_disabled

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "status": "INTERNAL_SERVER_ERROR",
            "code": 500,
            "message": "Internal server error",
            "details": [],
        }
    }
    assert "secret SQL statement" not in response.text
    records = [
        record
        for record in caplog.records
        if record.name == "mosemo.exception_handlers"
    ]
    assert len(records) == 1
    assert records[0].getMessage() == "Unhandled API exception"
    assert records[0].exc_info is not None


def test_controlled_exceptions_are_not_logged_as_unexpected(caplog) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/controlled")
    def controlled() -> None:
        raise ApiException(ErrorCode.INTERNAL_SERVER_ERROR)

    @app.get("/validated")
    def validated(value: int) -> int:
        return value

    caplog.set_level(logging.ERROR, logger="mosemo.exception_handlers")
    with TestClient(app, raise_server_exceptions=False) as client:
        controlled_response = client.get("/controlled")
        validation_response = client.get("/validated")

    assert controlled_response.status_code == 500
    assert validation_response.status_code == 422
    assert not [
        record
        for record in caplog.records
        if record.name == "mosemo.exception_handlers"
    ]

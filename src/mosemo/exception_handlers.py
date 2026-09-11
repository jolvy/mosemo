import logging

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mosemo.exceptions import (
    ApiException,
    ErrorCode,
)
from mosemo.responses import api_exception_response, error_response
from mosemo.schemas import ValidationDetail

logger = logging.getLogger(__name__)


def _validation_details(exc: RequestValidationError) -> list[ValidationDetail]:
    details = [
        ValidationDetail(
            loc=error["loc"],
            msg=error["msg"],
            type=error["type"],
        )
        for error in exc.errors()
    ]
    if details:
        return details

    raise ValueError("RequestValidationError must contain at least one error")


def _unexpected_exception_response(exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API exception", exc_info=exc)
    return error_response(ErrorCode.INTERNAL_SERVER_ERROR)


async def api_exception_handler(
    _request: Request,
    exc: ApiException,
) -> JSONResponse:
    headers = (
        {"WWW-Authenticate": "Bearer"}
        if exc.spec is ErrorCode.AUTH_INVALID_ACCESS_TOKEN
        else None
    )
    return api_exception_response(exc, headers=headers)


async def request_validation_exception_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return error_response(
        ErrorCode.INVALID_ARGUMENT,
        details=_validation_details(exc),
    )


def _is_invalid_encoding(exc: StarletteHTTPException) -> bool:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, UnicodeDecodeError):
            return True
        cause = cause.__cause__
    return False


async def http_exception_handler_for_public_api(
    request: Request,
    exc: StarletteHTTPException,
) -> Response:
    if exc.status_code == 404:
        return error_response(ErrorCode.REQUEST_ROUTE_NOT_FOUND)

    if exc.status_code == 405:
        response = error_response(ErrorCode.REQUEST_METHOD_NOT_ALLOWED)
        if exc.headers is not None:
            response.headers.update(exc.headers)
        return response

    if exc.status_code == 400 and _is_invalid_encoding(exc):
        return error_response(
            ErrorCode.INVALID_ARGUMENT,
            details=[
                ValidationDetail(
                    loc=["body"],
                    msg="Invalid request body encoding",
                    type="invalid_encoding",
                )
            ],
        )

    if 400 <= exc.status_code <= 599:
        return _unexpected_exception_response(exc)

    return await http_exception_handler(request, exc)


async def unexpected_exception_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return _unexpected_exception_response(exc)


def register_exception_handlers(app: FastAPI) -> None:
    app.exception_handler(ApiException)(api_exception_handler)
    app.exception_handler(RequestValidationError)(request_validation_exception_handler)
    app.exception_handler(StarletteHTTPException)(http_exception_handler_for_public_api)
    app.exception_handler(Exception)(unexpected_exception_handler)

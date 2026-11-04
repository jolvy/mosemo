import logging
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mosemo.exceptions import ErrorCode
from mosemo.shared.errors import ApiException, CommonErrorCode
from mosemo.shared.presentation.responses import error_response
from mosemo.shared.presentation.schemas import ValidationDetail

logger = logging.getLogger(__name__)

_MAX_HTTP_ERROR_STATUS = 599


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
    return error_response(CommonErrorCode.INTERNAL_SERVER_ERROR)


async def api_exception_handler(
    _request: Request,
    exc: ApiException,
) -> JSONResponse:
    headers = None
    if exc.spec is CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN:
        headers = {"WWW-Authenticate": "Bearer"}
    elif exc.spec in {
        ErrorCode.ACTIVITY_TIMELINE_BUSY,
        ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE,
    }:
        headers = {"Retry-After": "1"}
    return error_response(exc.spec, details=exc.details, headers=headers)


async def request_validation_exception_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return error_response(
        CommonErrorCode.INVALID_ARGUMENT,
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
    if exc.status_code == HTTPStatus.NOT_FOUND:
        return error_response(CommonErrorCode.REQUEST_ROUTE_NOT_FOUND)

    if exc.status_code == HTTPStatus.METHOD_NOT_ALLOWED:
        response = error_response(CommonErrorCode.REQUEST_METHOD_NOT_ALLOWED)
        if exc.headers is not None:
            response.headers.update(exc.headers)
        return response

    if exc.status_code == HTTPStatus.BAD_REQUEST and _is_invalid_encoding(exc):
        return error_response(
            CommonErrorCode.INVALID_ARGUMENT,
            details=[
                ValidationDetail(
                    loc=["body"],
                    msg="Invalid request body encoding",
                    type="invalid_encoding",
                )
            ],
        )

    if HTTPStatus.BAD_REQUEST <= exc.status_code <= _MAX_HTTP_ERROR_STATUS:
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

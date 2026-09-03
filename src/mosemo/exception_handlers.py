from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mosemo.exceptions import MosemoApiException


async def mosemo_api_exception_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, MosemoApiException):
        raise exc

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        MosemoApiException,
        mosemo_api_exception_handler,
    )

from collections.abc import Mapping, Sequence

from fastapi.responses import JSONResponse

from mosemo.exceptions import ApiException, ErrorSpec
from mosemo.schemas import ErrorPayload, ErrorResponse, ValidationDetail


def error_response(
    spec: ErrorSpec,
    *,
    details: Sequence[ValidationDetail] = (),
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    content = ErrorResponse(
        error=ErrorPayload(
            status=spec.status,
            code=spec.code,
            message=spec.message,
            details=list(details),
        )
    ).model_dump(mode="json")
    return JSONResponse(
        status_code=spec.code,
        content=content,
        headers=dict(headers) if headers is not None else None,
    )


def api_exception_response(
    error: ApiException,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return error_response(error.spec, details=error.details, headers=headers)

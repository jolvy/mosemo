from collections.abc import Sequence
from dataclasses import dataclass

from mosemo.schemas import ValidationDetail


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    status: str
    code: int
    message: str


class ErrorCode:
    AUTH_INVALID_AUTHORIZATION_CODE = ErrorSpec(
        status="AUTH_INVALID_AUTHORIZATION_CODE",
        code=400,
        message="Invalid or expired authorization code",
    )
    AUTH_INVALID_OAUTH_CONTEXT = ErrorSpec(
        status="AUTH_INVALID_OAUTH_CONTEXT",
        code=400,
        message="Invalid or expired OAuth login context",
    )
    AUTH_INVALID_ACCESS_TOKEN = ErrorSpec(
        status="AUTH_INVALID_ACCESS_TOKEN",
        code=401,
        message="Invalid or expired access token",
    )
    REQUEST_ROUTE_NOT_FOUND = ErrorSpec(
        status="REQUEST_ROUTE_NOT_FOUND",
        code=404,
        message="API route not found",
    )
    REQUEST_METHOD_NOT_ALLOWED = ErrorSpec(
        status="REQUEST_METHOD_NOT_ALLOWED",
        code=405,
        message="Method not allowed",
    )
    INVALID_ARGUMENT = ErrorSpec(
        status="INVALID_ARGUMENT",
        code=422,
        message="Request validation failed.",
    )
    INTERNAL_SERVER_ERROR = ErrorSpec(
        status="INTERNAL_SERVER_ERROR",
        code=500,
        message="Internal server error",
    )


class ApiException(Exception):
    def __init__(
        self,
        spec: ErrorSpec,
        *,
        details: Sequence[ValidationDetail] = (),
    ) -> None:
        normalized_details = tuple(details)

        super().__init__(spec.message)
        self.spec = spec
        self.details = normalized_details

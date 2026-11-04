from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from mosemo.shared.presentation.schemas import ValidationDetail


@dataclass(frozen=True, slots=True, eq=False)
class ErrorSpec:
    code: int
    message: str


class ApiErrorCode(Enum):
    """Base for public error code enums. Members are defined by subclasses."""

    @property
    def status(self) -> str:
        return self.name

    @property
    def code(self) -> int:
        return self.value.code

    @property
    def message(self) -> str:
        return self.value.message


class CommonErrorCode(ApiErrorCode):
    AUTH_INVALID_ACCESS_TOKEN = ErrorSpec(
        code=401,
        message="Invalid or expired access token",
    )
    REQUEST_ROUTE_NOT_FOUND = ErrorSpec(
        code=404,
        message="API route not found",
    )
    REQUEST_METHOD_NOT_ALLOWED = ErrorSpec(
        code=405,
        message="Method not allowed",
    )
    INVALID_ARGUMENT = ErrorSpec(
        code=422,
        message="Request validation failed.",
    )
    INTERNAL_SERVER_ERROR = ErrorSpec(
        code=500,
        message="Internal server error",
    )


class ApiException(Exception):
    def __init__(
        self,
        spec: ApiErrorCode,
        *,
        details: Sequence[ValidationDetail] = (),
    ) -> None:
        normalized_details = tuple(details)

        super().__init__(spec.message)
        self.spec = spec
        self.details = normalized_details

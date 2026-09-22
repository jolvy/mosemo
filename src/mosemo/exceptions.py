from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from mosemo.schemas import ValidationDetail


@dataclass(frozen=True, slots=True, eq=False)
class ErrorSpec:
    code: int
    message: str


class ErrorCode(Enum):
    AUTH_INVALID_AUTHORIZATION_CODE = ErrorSpec(
        code=400,
        message="Invalid or expired authorization code",
    )
    AUTH_INVALID_OAUTH_CONTEXT = ErrorSpec(
        code=400,
        message="Invalid or expired OAuth login context",
    )
    AUTH_INVALID_ACCESS_TOKEN = ErrorSpec(
        code=401,
        message="Invalid or expired access token",
    )
    ACTIVITY_DEVICE_NOT_FOUND = ErrorSpec(
        code=404,
        message="Activity device not found",
    )
    ACTIVITY_EVENT_ID_CONFLICT = ErrorSpec(
        code=409,
        message="Activity event ID conflicts with a stored record",
    )
    ACTIVITY_SEQUENCE_CONFLICT = ErrorSpec(
        code=409,
        message="Activity sequence conflicts with a stored record",
    )
    ACTIVITY_TIMELINE_BUSY = ErrorSpec(
        code=503,
        message="Activity timeline is busy",
    )
    ACTIVITY_SEGMENT_NOT_FOUND = ErrorSpec(
        code=404,
        message="Activity segment not found",
    )
    ACTIVITY_SEGMENT_NOT_LABELABLE = ErrorSpec(
        code=409,
        message="Activity segment is not labelable",
    )
    ACTIVITY_SEGMENT_CHANGED = ErrorSpec(
        code=409,
        message="Activity segment changed; refresh before confirming",
    )
    LABEL_NOT_AVAILABLE = ErrorSpec(
        code=404,
        message="Label not found or inactive",
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

    @property
    def status(self) -> str:
        return self.name

    @property
    def code(self) -> int:
        return self.value.code

    @property
    def message(self) -> str:
        return self.value.message


class ApiException(Exception):
    def __init__(
        self,
        spec: ErrorCode,
        *,
        details: Sequence[ValidationDetail] = (),
    ) -> None:
        normalized_details = tuple(details)

        super().__init__(spec.message)
        self.spec = spec
        self.details = normalized_details

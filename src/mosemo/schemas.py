import re
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictInt,
    StrictStr,
    WithJsonSchema,
)
from pydantic.alias_generators import to_camel

OBSERVATION_TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
PUBLIC_TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


def _parse_observation_timestamp(value: Any) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(OBSERVATION_TIMESTAMP_PATTERN, value) is None
    ):
        raise ValueError(
            "observation timestamps must be RFC 3339 UTC strings ending in Z"
        )

    return datetime.fromisoformat(f"{value[:-1]}+00:00")


def _normalize_public_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("public API datetimes must be timezone-aware")

    return value.astimezone(UTC)


def _serialize_public_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("public API datetimes must be timezone-aware")

    return (
        value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


ObservationTimestamp = Annotated[
    datetime,
    BeforeValidator(_parse_observation_timestamp),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": OBSERVATION_TIMESTAMP_PATTERN,
        },
        mode="validation",
    ),
]

PublicTimestamp = Annotated[
    datetime,
    AfterValidator(_normalize_public_timestamp),
    PlainSerializer(
        _serialize_public_timestamp,
        return_type=str,
        when_used="json",
    ),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": PUBLIC_TIMESTAMP_PATTERN,
        },
        mode="serialization",
    ),
]


class ApiRequestModel(BaseModel):
    """Mosemo 공개 JSON 요청이 사용하는 공통 모델입니다."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        extra="forbid",
    )


class ApiResponseModel(BaseModel):
    """Mosemo 공개 JSON 응답이 사용하는 공통 모델입니다."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


class ValidationDetail(ApiResponseModel):
    """RequestValidationError에서 선택한 공개 검증 오류 정보입니다."""

    model_config = ConfigDict(extra="forbid")

    loc: list[StrictStr | StrictInt] = Field(
        description=(
            "Pydantic validation error의 loc 원문입니다. JSON에서는 문자열과 "
            "정수 segment의 배열입니다."
        )
    )
    msg: StrictStr = Field(description="Pydantic validation error의 msg 원문입니다.")
    type: StrictStr = Field(
        description="Pydantic validation error의 type 원문 또는 서버 정의 오류 유형입니다."
    )


class ErrorPayload(ApiResponseModel):
    """공개 오류 envelope 안의 공통 payload입니다."""

    model_config = ConfigDict(extra="forbid")

    status: StrictStr = Field(
        description="클라이언트가 분기할 애플리케이션 오류 식별자입니다."
    )
    code: int = Field(description="실제 HTTP response status와 같은 오류 코드입니다.")
    message: StrictStr = Field(description="개발자용 영문 오류 설명입니다.")
    details: list[ValidationDetail] = Field(
        description="검증 오류 상세 배열이며 일반 오류에서는 비어 있습니다."
    )


class ErrorResponse(ApiResponseModel):
    """Mosemo 공개 API가 반환하는 공통 오류 response입니다."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorPayload = Field(description="공개 오류 payload입니다.")

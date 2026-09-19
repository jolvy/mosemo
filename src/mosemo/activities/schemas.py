from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)

from mosemo.activities.enums import CollectionState, RecordType, SegmentType
from mosemo.schemas import (
    OBSERVATION_TIMESTAMP_PATTERN,
    ApiRequestModel,
    ApiResponseModel,
    ObservationTimestamp,
    PublicTimestamp,
)
from mosemo.timezones import Timezone


class ActivityRequestModel(ApiRequestModel):
    """Base model for activity collection request data."""


class CapturedString(ActivityRequestModel):
    """A string value captured by the client."""

    status: Literal["captured"] = Field(
        description="값을 정상적으로 수집했음을 나타냅니다."
    )
    value: str = Field(description="클라이언트가 수집한 원문 값입니다.")


class CapturedText(CapturedString):
    """Captured display text, including optional truncation metadata."""

    truncated: bool = Field(
        default=False,
        description="원문 앞부분만 보관했는지를 나타냅니다.",
    )
    original_byte_length: int | None = Field(
        default=None,
        ge=0,
        description="잘리기 전 원문의 UTF-8 바이트 길이입니다.",
    )

    @model_validator(mode="after")
    def validate_truncation_metadata(self) -> CapturedText:
        if self.truncated and self.original_byte_length is None:
            raise ValueError("original_byte_length is required when truncated is true")
        if not self.truncated and self.original_byte_length is not None:
            raise ValueError(
                "original_byte_length is only allowed when truncated is true"
            )
        if self.original_byte_length is not None and self.original_byte_length <= len(
            self.value.encode("utf-8")
        ):
            raise ValueError(
                "original_byte_length must exceed the captured value byte length"
            )
        return self


class AbsentValue(ActivityRequestModel):
    """A value that does not exist at the observation point."""

    status: Literal["absent"] = Field(
        description="관찰 시점에 해당 값이 없었음을 나타냅니다."
    )


class UnavailableValue(ActivityRequestModel):
    """A value that the client could not read."""

    status: Literal["unavailable"] = Field(
        description="해당 값을 읽을 수 없었음을 나타냅니다."
    )
    reason: str = Field(
        min_length=1,
        description="값을 읽을 수 없었던 이유입니다.",
    )


class RedactedValue(ActivityRequestModel):
    """A value intentionally removed by the client privacy policy."""

    status: Literal["redacted"] = Field(
        description="개인정보 정책에 따라 값을 저장하지 않았음을 나타냅니다."
    )
    reason: str = Field(
        min_length=1,
        description="값을 저장하지 않은 개인정보 정책상의 이유입니다.",
    )


AppField = Annotated[
    CapturedString | AbsentValue | UnavailableValue,
    Field(discriminator="status"),
]
TitleField = Annotated[
    CapturedText | AbsentValue | UnavailableValue | RedactedValue,
    Field(discriminator="status"),
]
UrlField = Annotated[
    CapturedString | AbsentValue | UnavailableValue | RedactedValue,
    Field(discriminator="status"),
]


class AppContext(ActivityRequestModel):
    """Application identifiers observed independently by the client."""

    bundle_id: AppField = Field(description="포커싱된 앱의 bundle ID 상태와 값입니다.")
    name: AppField = Field(description="포커싱된 앱의 표시 이름 상태와 값입니다.")


class CapturedWindow(ActivityRequestModel):
    """A focused window and its captured title."""

    status: Literal["captured"] = Field(
        description="포커스 창과 제목을 정상적으로 수집했음을 나타냅니다."
    )
    title: CapturedText = Field(description="포커스 창의 제목입니다.")


class AbsentWindow(ActivityRequestModel):
    """An observation where the focused application has no window."""

    status: Literal["absent"] = Field(
        description="포커싱된 앱에 창이 없었음을 나타냅니다."
    )


class UnavailableWindow(ActivityRequestModel):
    """A window that the client could not inspect."""

    status: Literal["unavailable"] = Field(
        description="포커스 창을 읽을 수 없었음을 나타냅니다."
    )
    reason: str = Field(
        min_length=1,
        description="포커스 창을 읽을 수 없었던 이유입니다.",
    )


WindowContext = Annotated[
    CapturedWindow | AbsentWindow | UnavailableWindow,
    Field(discriminator="status"),
]


class NotApplicableWebContext(ActivityRequestModel):
    """A non-browser application context."""

    kind: Literal["not_applicable"] = Field(
        description="포커싱된 앱에 웹 문맥을 적용할 수 없음을 나타냅니다."
    )


class BrowserWebContext(ActivityRequestModel):
    """Browser tab and URL values observed independently by the client."""

    kind: Literal["browser"] = Field(
        description="브라우저에서 관찰한 웹 문맥임을 나타냅니다."
    )
    tab_title: TitleField = Field(
        description="포커싱된 브라우저 탭 제목의 상태와 값입니다."
    )
    url: UrlField = Field(description="포커싱된 브라우저 탭 URL의 상태와 값입니다.")


WebContext = Annotated[
    NotApplicableWebContext | BrowserWebContext,
    Field(discriminator="kind"),
]


class DetailedActivityContext(ActivityRequestModel):
    """A privacy-filtered activity context with observable identifiers."""

    kind: Literal["detailed"] = Field(
        description="식별 가능한 상세 활동 문맥임을 나타냅니다."
    )
    app: AppContext = Field(description="포커싱된 앱의 관찰 문맥입니다.")
    window: WindowContext = Field(description="포커스 창의 관찰 문맥입니다.")
    web: WebContext = Field(description="브라우저 웹 활동의 관찰 문맥입니다.")


class OpaqueActivityContext(ActivityRequestModel):
    """A normal observation whose identifying details were removed."""

    kind: Literal["opaque"] = Field(
        description="식별 정보를 제거한 정상 활동 관찰임을 나타냅니다."
    )


ActivityContext = Annotated[
    DetailedActivityContext | OpaqueActivityContext,
    Field(discriminator="kind"),
]


class ActivityRecordBase(ActivityRequestModel):
    """Fields shared by every ordered activity collection record."""

    device_id: UUID = Field(description="서버에 등록된 Device 식별자입니다.")
    event_id: UUID = Field(description="레코드 중복 제거에 사용하는 식별자입니다.")
    sequence: int = Field(
        ge=0,
        description="Device 안에서 단조 증가하는 레코드 순번입니다.",
    )
    observed_at: ObservationTimestamp = Field(
        description="클라이언트 벽시계로 기록한 UTC 관찰 시각입니다."
    )
    timezone_id: Timezone = Field(
        description="관찰 당시의 지원 시간대 식별자입니다.",
    )
    utc_offset_minutes: int = Field(
        description="관찰 당시 UTC와 현지 시간의 차이(분)입니다."
    )


class ActivityObservation(ActivityRecordBase):
    """A complete snapshot of the focused activity at one point in time."""

    record_type: Literal[RecordType.ACTIVITY_OBSERVATION] = Field(
        description="활동 전체 스냅샷 레코드임을 나타냅니다."
    )
    context: ActivityContext = Field(
        description="개인정보 필터링을 마친 전체 활동 문맥입니다."
    )


class CollectionStateChanged(ActivityRecordBase):
    """A change in whether the client can observe activity."""

    record_type: Literal[RecordType.COLLECTION_STATE_CHANGED] = Field(
        description="수집 가능 상태 변경 레코드임을 나타냅니다."
    )
    state: CollectionState = Field(description="변경된 활동 수집 가능 상태입니다.")
    reason: str = Field(
        min_length=1,
        description="수집 상태가 변경된 이유입니다.",
    )


ActivityRecord = Annotated[
    ActivityObservation | CollectionStateChanged,
    Field(discriminator="record_type"),
]


class ActivityCreateResponse(ApiResponseModel):
    """활동 레코드 저장 결과입니다."""

    event_id: UUID = Field(
        description="저장하거나 중복 확인한 활동 이벤트 식별자입니다."
    )
    status: Literal["accepted"] = Field(
        description="서버가 활동 레코드를 영구 저장했음을 나타냅니다."
    )
    received_at: PublicTimestamp = Field(
        description="활동 레코드가 서버에 최초 저장된 시각입니다."
    )


def _timeline_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timeline timestamps must be timezone-aware")
    return value.astimezone(UTC)


TimelineTimestamp = Annotated[
    datetime,
    AfterValidator(_timeline_timestamp),
    PlainSerializer(
        lambda value: value.isoformat().replace("+00:00", "Z"),
        return_type=str,
        when_used="json",
    ),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": OBSERVATION_TIMESTAMP_PATTERN,
        },
        mode="serialization",
    ),
]


class ActivitySegmentResponse(ApiResponseModel):
    """실제로 관찰된 동일 문맥의 활동 구간입니다."""

    segment_id: UUID = Field(description="재구축 시 바뀔 수 있는 구간 식별자입니다.")
    segment_type: Literal[SegmentType.ACTIVITY] = Field(
        description="활동 구간 종류입니다."
    )
    started_at: TimelineTimestamp = Field(description="첫 활동 관찰 시각입니다.")
    ended_at: TimelineTimestamp | None = Field(
        description="관찰로 확인되거나 침묵으로 닫힌 종료 시각입니다."
    )
    last_observed_at: TimelineTimestamp = Field(
        description="같은 문맥이 마지막으로 실제 관찰된 시각입니다."
    )
    context: ActivityContext = Field(
        description="개인정보 필터 후 원본 전체 문맥입니다."
    )


class CaptureGapResponse(ApiResponseModel):
    """명시적 수집 중단으로 관찰할 수 없었던 구간입니다."""

    segment_id: UUID = Field(description="재구축 시 바뀔 수 있는 구간 식별자입니다.")
    segment_type: Literal[SegmentType.CAPTURE_GAP] = Field(
        description="수집 공백 종류입니다."
    )
    started_at: TimelineTimestamp = Field(description="첫 suspended 관찰 시각입니다.")
    ended_at: TimelineTimestamp | None = Field(
        description="다음 실제 활동 관찰로 확인된 종료 시각입니다."
    )
    reason: str = Field(description="첫 suspended의 수집 중단 사유입니다.")


TimelineSegmentResponse = Annotated[
    ActivitySegmentResponse | CaptureGapResponse,
    Field(discriminator="segment_type"),
]

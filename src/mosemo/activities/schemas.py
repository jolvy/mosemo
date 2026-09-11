from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ActivityRequestModel(BaseModel):
    """Base model for activity collection request data."""

    model_config = ConfigDict(extra="forbid")


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

    event_id: UUID = Field(description="레코드 중복 제거에 사용하는 식별자입니다.")
    sequence: int = Field(
        ge=0,
        description="수집 스트림 안에서 단조 증가하는 레코드 순번입니다.",
    )
    observed_at: AwareDatetime = Field(
        description="클라이언트 벽시계로 기록한 UTC 관찰 시각입니다."
    )
    timezone_id: str = Field(
        min_length=1,
        description="관찰 당시의 IANA 시간대 식별자입니다.",
    )
    utc_offset_minutes: int = Field(
        description="관찰 당시 UTC와 현지 시간의 차이(분)입니다."
    )
    clock_epoch_id: UUID = Field(
        description="단조 시계 값의 비교 가능 구간을 식별합니다."
    )
    monotonic_ns: int = Field(
        ge=0,
        description="clock epoch 안에서 측정한 단조 시계 값(나노초)입니다.",
    )

    @model_validator(mode="after")
    def validate_observed_at_is_utc(self) -> ActivityRecordBase:
        if self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("observed_at must use UTC")
        return self


class ActivityObservation(ActivityRecordBase):
    """A complete snapshot of the focused activity at one point in time."""

    record_type: Literal["activity_observation"] = Field(
        description="활동 전체 스냅샷 레코드임을 나타냅니다."
    )
    context: ActivityContext = Field(
        description="개인정보 필터링을 마친 전체 활동 문맥입니다."
    )


class CollectionStateChanged(ActivityRecordBase):
    """A change in whether the client can observe activity."""

    record_type: Literal["collection_state_changed"] = Field(
        description="수집 가능 상태 변경 레코드임을 나타냅니다."
    )
    state: Literal["active", "suspended"] = Field(
        description="변경된 활동 수집 가능 상태입니다."
    )
    reason: str = Field(
        min_length=1,
        description="수집 상태가 변경된 이유입니다.",
    )


ActivityRecord = Annotated[
    ActivityObservation | CollectionStateChanged,
    Field(discriminator="record_type"),
]


class ActivityBatchRequest(ActivityRequestModel):
    """An ordered FIFO batch from one registered device and collection stream."""

    batch_id: UUID = Field(
        description="동일 본문 재전송을 식별하는 요청 단위 식별자입니다."
    )
    device_registration_id: UUID = Field(
        description="서버에 등록된 수집 기기의 식별자입니다."
    )
    collection_stream_id: UUID = Field(
        description="레코드 순번이 유효한 연속 수집 계보의 식별자입니다."
    )
    records: list[ActivityRecord] = Field(
        min_length=1,
        description="한 수집 스트림에서 FIFO 순서로 묶은 활동 레코드입니다.",
    )

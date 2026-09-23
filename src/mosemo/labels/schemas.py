from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from mosemo.activities.schemas import (
    DetailedActivityContext,
    OpaqueActivityContext,
    TimelineTimestamp,
)
from mosemo.schemas import ApiRequestModel, ApiResponseModel, PublicTimestamp

SEGMENT_VERSION_PATTERN = r"^[0-9a-f]{64}$"


class LabelSelectionRequest(ApiRequestModel):
    """라벨 하나를 선택하는 확정 요청입니다."""

    kind: Literal["label"] = Field(description="활동에 확정할 라벨 선택입니다.")
    label_id: UUID = Field(description="확정할 활성 라벨 식별자입니다.")


class UnclassifiedSelectionRequest(ApiRequestModel):
    """미분류 상태를 선택하는 확정 요청입니다."""

    kind: Literal["unclassified"] = Field(
        description="활동을 미분류로 확정하는 선택입니다."
    )


LabelSelectionRequestUnion = Annotated[
    LabelSelectionRequest | UnclassifiedSelectionRequest,
    Field(discriminator="kind"),
]


class ActivityLabelConfirmationRequest(ApiRequestModel):
    """관찰 구간 하나의 최신 라벨 확정 요청입니다."""

    segment_version: str = Field(
        min_length=64,
        max_length=64,
        pattern=SEGMENT_VERSION_PATTERN,
        description="클라이언트가 조회한 관찰 구간의 불투명 version입니다.",
    )
    selection: LabelSelectionRequestUnion = Field(
        description="라벨 또는 미분류 확정 선택입니다."
    )


class LabelSelectionResponse(ApiResponseModel):
    """응답에 포함되는 라벨 확정 선택입니다."""

    kind: Literal["label"] = Field(description="확정된 라벨 선택입니다.")
    label_id: UUID = Field(description="확정된 라벨 식별자입니다.")


class UnclassifiedSelectionResponse(ApiResponseModel):
    """응답에 포함되는 미분류 확정 선택입니다."""

    kind: Literal["unclassified"] = Field(
        description="활동이 미분류로 확정되었음을 나타냅니다."
    )


LabelSelectionResponseUnion = Annotated[
    LabelSelectionResponse | UnclassifiedSelectionResponse,
    Field(discriminator="kind"),
]


class PendingActivityLabelStateResponse(ApiResponseModel):
    """아직 라벨을 확정하지 않은 관찰 구간 상태입니다."""

    segment_id: UUID = Field(description="현재 관찰 활동 구간 식별자입니다.")
    segment_version: str = Field(description="현재 관찰 구간의 불투명 version입니다.")
    state: Literal["pending"] = Field(
        description="아직 라벨 또는 미분류를 확정하지 않은 상태입니다."
    )


class ConfirmedActivityLabelStateResponse(ApiResponseModel):
    """사용자가 라벨 또는 미분류를 확정한 관찰 구간 상태입니다."""

    segment_id: UUID = Field(description="현재 관찰 활동 구간 식별자입니다.")
    segment_version: str = Field(description="현재 관찰 구간의 불투명 version입니다.")
    state: Literal["confirmed"] = Field(
        description="사용자가 라벨 또는 미분류를 확정한 상태입니다."
    )
    selection: LabelSelectionResponseUnion = Field(
        description="사용자가 확정한 라벨 또는 미분류 선택입니다."
    )
    confirmed_at: PublicTimestamp = Field(description="최초 확정 시각입니다.")
    updated_at: PublicTimestamp = Field(description="마지막 정정 시각입니다.")


ActivityLabelStateResponse = Annotated[
    PendingActivityLabelStateResponse | ConfirmedActivityLabelStateResponse,
    Field(discriminator="state"),
]


class LabelTimelineSegmentResponse(ApiResponseModel):
    """A closed observation segment included in a label timeline group."""

    segment_id: UUID = Field(
        description="재구축 시 바뀔 수 있는 관찰 구간 식별자입니다."
    )
    segment_version: str = Field(description="현재 관찰 구간의 불투명 version입니다.")
    started_at: TimelineTimestamp = Field(description="관찰 구간 시작 시각입니다.")
    ended_at: TimelineTimestamp = Field(
        description="종료가 확인된 관찰 구간 시각입니다."
    )
    last_observed_at: TimelineTimestamp = Field(
        description="마지막 실제 관찰 시각입니다."
    )
    context: DetailedActivityContext = Field(
        description="개인정보 필터 후 상세 관찰 문맥입니다."
    )


class LabelTimelineLabelSelectionResponse(ApiResponseModel):
    """A confirmed label and its current display name."""

    kind: Literal["label"] = Field(description="확정된 라벨 선택입니다.")
    label_id: UUID = Field(description="확정 라벨 식별자입니다.")
    display_name: str = Field(description="현재 라벨 표시 이름입니다.")


LabelTimelineSelectionResponse = Annotated[
    LabelTimelineLabelSelectionResponse | UnclassifiedSelectionResponse,
    Field(discriminator="kind"),
]


class ActivityGroupResponse(ApiResponseModel):
    """Adjacent closed detailed activity segments with matching label state."""

    item_type: Literal["activity_group"] = Field(description="라벨 활동 묶음입니다.")
    started_at: TimelineTimestamp = Field(description="묶음의 첫 구간 시작 시각입니다.")
    ended_at: TimelineTimestamp = Field(
        description="묶음의 마지막 구간 종료 시각입니다."
    )
    state: Literal["pending", "confirmed"] = Field(
        description="묶음의 공통 라벨 확정 상태입니다."
    )
    selection: LabelTimelineSelectionResponse | None = Field(
        default=None,
        description="확정 상태일 때의 라벨 또는 미분류 선택입니다.",
    )
    segments: list[LabelTimelineSegmentResponse] = Field(
        min_length=1,
        description="묶음에 포함된 닫힌 원본 구간을 관찰 순서대로 담습니다.",
    )

    @model_validator(mode="after")
    def validate_selection_matches_state(self) -> ActivityGroupResponse:
        if (self.state == "pending") != (self.selection is None):
            raise ValueError("selection must be present only for confirmed groups")
        return self


class InProgressActivityResponse(ApiResponseModel):
    """A detailed activity segment whose end is not yet known."""

    item_type: Literal["in_progress_activity"] = Field(
        description="아직 종료가 확인되지 않은 상세 활동입니다."
    )
    segment_id: UUID = Field(description="관찰 활동 구간 식별자입니다.")
    started_at: TimelineTimestamp = Field(description="관찰 구간 시작 시각입니다.")
    ended_at: None = Field(description="종료가 아직 확인되지 않아 항상 null입니다.")
    last_observed_at: TimelineTimestamp = Field(
        description="마지막 실제 관찰 시각입니다."
    )
    context: DetailedActivityContext = Field(
        description="개인정보 필터 후 상세 관찰 문맥입니다."
    )


class OpaqueActivityResponse(ApiResponseModel):
    """An activity observation with identifying details removed."""

    item_type: Literal["opaque_activity"] = Field(description="불투명 활동입니다.")
    segment_id: UUID = Field(description="관찰 활동 구간 식별자입니다.")
    started_at: TimelineTimestamp = Field(description="관찰 구간 시작 시각입니다.")
    ended_at: TimelineTimestamp | None = Field(description="종료가 확인된 시각입니다.")
    last_observed_at: TimelineTimestamp = Field(
        description="마지막 실제 관찰 시각입니다."
    )
    context: OpaqueActivityContext = Field(
        description="식별 정보가 제거된 관찰 문맥입니다."
    )


class LabelTimelineCaptureGapResponse(ApiResponseModel):
    """A period when activity collection was explicitly suspended."""

    item_type: Literal["capture_gap"] = Field(description="명시적인 수집 공백입니다.")
    segment_id: UUID = Field(description="수집 공백 구간 식별자입니다.")
    started_at: TimelineTimestamp = Field(description="수집 공백 시작 시각입니다.")
    ended_at: TimelineTimestamp | None = Field(description="수집 공백 종료 시각입니다.")
    reason: str = Field(description="첫 수집 중단의 사유입니다.")


LabelTimelineItemResponse = Annotated[
    ActivityGroupResponse
    | InProgressActivityResponse
    | OpaqueActivityResponse
    | LabelTimelineCaptureGapResponse,
    Field(discriminator="item_type"),
]

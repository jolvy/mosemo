from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

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

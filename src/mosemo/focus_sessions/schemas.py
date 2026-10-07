from uuid import UUID

from pydantic import Field

from mosemo.activities.schemas import TimelineTimestamp
from mosemo.schemas import ApiRequestModel, ApiResponseModel, ObservationTimestamp


class FocusSessionCreateRequest(ApiRequestModel):
    """집중 세션을 시작하는 재시도 가능한 요청입니다."""

    session_id: UUID = Field(
        description="재시도에도 유지하는 클라이언트 발급 세션 UUID입니다."
    )
    device_id: UUID = Field(description="세션을 시작한 등록 기기 식별자입니다.")
    started_at: ObservationTimestamp = Field(description="UTC 세션 시작 시각입니다.")
    target_seconds: int = Field(
        ge=0,
        le=359999,
        description="0이면 경과 시간, 양수이면 카운트다운 목표 초입니다.",
    )


class FocusSessionCompleteRequest(ApiRequestModel):
    """종료한 세션의 작업 시간과 라벨을 확정하는 요청입니다."""

    ended_at: ObservationTimestamp = Field(
        description="일시중지 포함 UTC 세션 종료 시각입니다."
    )
    work_seconds: int = Field(
        ge=0, le=2147483647, description="일시중지 시간을 제외한 실제 작업 초입니다."
    )
    label_id: UUID = Field(description="계정의 활성 라벨 식별자입니다.")
    description: str = Field(
        default="", max_length=10000, description="선택적인 작업 설명입니다."
    )


class FocusSessionResponse(ApiResponseModel):
    """진행 중이거나 완료한 집중 세션입니다."""

    session_id: UUID = Field(description="집중 세션 식별자입니다.")
    device_id: UUID = Field(description="세션을 시작한 등록 기기 식별자입니다.")
    started_at: TimelineTimestamp = Field(description="UTC 세션 시작 시각입니다.")
    ended_at: TimelineTimestamp | None = Field(
        description="UTC 종료 시각입니다. 기록 완료 전에는 null입니다."
    )
    target_seconds: int = Field(description="목표 초입니다. 0이면 경과 시간입니다.")
    work_seconds: int | None = Field(
        description="일시중지를 제외한 작업 초입니다. 완료 전에는 null입니다."
    )
    label_id: UUID | None = Field(
        description="완료 시 확정된 라벨입니다. 완료 전에는 null입니다."
    )
    description: str = Field(description="작업 설명입니다.")

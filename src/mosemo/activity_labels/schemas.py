from uuid import UUID

from pydantic import ConfigDict, Field

from mosemo.schemas import ApiResponseModel, PublicTimestamp


class LabelResponse(ApiResponseModel):
    """계정의 활성 또는 보관 라벨입니다."""

    model_config = ConfigDict(from_attributes=True)

    label_id: UUID = Field(description="라벨의 고유 식별자입니다.")
    display_name: str = Field(description="현재 라벨 표시 이름입니다.")
    created_at: PublicTimestamp = Field(description="라벨 생성 시각입니다.")
    updated_at: PublicTimestamp = Field(description="라벨 마지막 갱신 시각입니다.")
    archived_at: PublicTimestamp | None = Field(
        description="보관 시각입니다. 사용 중인 라벨이면 null입니다."
    )

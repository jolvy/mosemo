from uuid import UUID

from pydantic import Field

from mosemo.labels.domain.labels import Label
from mosemo.shared.presentation.schemas import ApiResponseModel, PublicTimestamp


class LabelResponse(ApiResponseModel):
    """계정의 활성 또는 보관 라벨입니다."""

    label_id: UUID = Field(description="라벨의 고유 식별자입니다.")
    display_name: str = Field(description="현재 라벨 표시 이름입니다.")
    created_at: PublicTimestamp = Field(description="라벨 생성 시각입니다.")
    updated_at: PublicTimestamp = Field(description="라벨 마지막 갱신 시각입니다.")
    archived_at: PublicTimestamp | None = Field(
        description="보관 시각입니다. 사용 중인 라벨이면 null입니다."
    )

    @classmethod
    def from_label(cls, label: Label) -> LabelResponse:
        return cls(
            label_id=label.id,
            display_name=label.display_name,
            created_at=label.created_at,
            updated_at=label.updated_at,
            archived_at=label.archived_at,
        )

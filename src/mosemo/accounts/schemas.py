from uuid import UUID

from pydantic import ConfigDict, Field

from mosemo.accounts.models import AccountProvider
from mosemo.schemas import ApiResponseModel, PublicTimestamp


class AccountResponse(ApiResponseModel):
    """현재 인증된 Mosemo 계정의 공개 정보입니다."""

    model_config = ConfigDict(from_attributes=True)

    account_id: UUID = Field(description="Mosemo 계정의 고유 식별자입니다.")
    provider: AccountProvider = Field(
        description="계정 인증에 사용한 외부 인증 제공자입니다."
    )
    timezone: str = Field(description="날짜 조회에 사용하는 계정의 IANA 시간대입니다.")
    created_at: PublicTimestamp = Field(description="계정이 생성된 시각입니다.")
    last_authenticated_at: PublicTimestamp = Field(
        description="외부 인증 제공자를 통해 마지막으로 인증한 시각입니다."
    )

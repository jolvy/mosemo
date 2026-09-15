from uuid import UUID

from pydantic import Field

from mosemo.schemas import ApiResponseModel


class DeviceCreateResponse(ApiResponseModel):
    """Device 등록 결과입니다."""

    device_id: UUID = Field(description="서버가 발급한 Device 식별자입니다.")

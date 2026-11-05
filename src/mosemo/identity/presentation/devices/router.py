from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status

from mosemo.identity.application.devices import RegisterDevice
from mosemo.identity.presentation.dependencies import SessionDep
from mosemo.identity.presentation.devices.schemas import DeviceCreateResponse
from mosemo.shared.errors import CommonErrorCode
from mosemo.shared.openapi import api_error_responses
from mosemo.shared.presentation.current_account import CurrentAccountDep

IdempotencyKeyHeader = Annotated[
    UUID,
    Header(
        alias="Idempotency-Key",
        description=(
            "하나의 Device 등록 시도를 식별하는 UUID입니다. 응답을 받기 전 "
            "재시도에는 같은 값을 사용합니다."
        ),
    ),
]


def create_router(*, register_device: RegisterDevice) -> APIRouter:
    router = APIRouter(prefix="/devices")

    @router.post(
        "",
        operation_id="devicesCreate",
        status_code=status.HTTP_201_CREATED,
        response_model=DeviceCreateResponse,
        summary="Device 등록",
        description=(
            "인증된 계정에 앱 설치를 Device로 등록하고 서버가 발급한 식별자를 "
            "반환합니다. 같은 계정과 Idempotency-Key의 재시도는 최초 식별자를 반환합니다."
        ),
        response_description="생성되었거나 동일한 재시도로 확인된 Device입니다.",
        responses=api_error_responses(
            CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
            CommonErrorCode.INVALID_ARGUMENT,
        ),
    )
    async def create_device(
        idempotency_key: IdempotencyKeyHeader,
        db_session: SessionDep,
        *,
        current_account: CurrentAccountDep,
    ) -> DeviceCreateResponse:
        device_id = await register_device.execute(
            db_session,
            account_id=current_account.account_id,
            idempotency_key=idempotency_key,
        )
        return DeviceCreateResponse(id=device_id)

    return router

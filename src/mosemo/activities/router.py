from typing import Never

from fastapi import APIRouter, status

from mosemo.activities.repository import ActivityStorageNotImplementedError
from mosemo.activities.schemas import ActivityRecord
from mosemo.dependencies import ActivityServiceDep, CurrentAccountDep
from mosemo.exceptions import ApiException, ErrorCode
from mosemo.openapi import api_error_responses

router = APIRouter(prefix="/activities")


@router.post(
    "",
    operation_id="activitiesCreate",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    response_model=None,
    summary="활동 레코드 생성",
    description=(
        "인증된 계정의 기기에서 관찰한 활동 또는 수집 상태 변경 한 건을 수신합니다. "
        "현재 저장 기능은 구현되지 않았습니다."
    ),
    response_description="활동 저장 기능이 아직 구현되지 않았습니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ErrorCode.INVALID_ARGUMENT,
        ErrorCode.ACTIVITY_INGEST_NOT_IMPLEMENTED,
    ),
)
async def create_activity(
    record: ActivityRecord,
    service: ActivityServiceDep,
    account: CurrentAccountDep,
) -> Never:
    try:
        return await service.create_activity(
            account_id=account.account_id,
            record=record,
        )
    except ActivityStorageNotImplementedError as exc:
        raise ApiException(ErrorCode.ACTIVITY_INGEST_NOT_IMPLEMENTED) from exc

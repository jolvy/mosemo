from fastapi import APIRouter, status

from mosemo.activities.schemas import ActivityCreateResponse, ActivityRecord
from mosemo.activities.service import (
    ActivityDeviceNotFoundError,
    ActivityEventIdConflictError,
    ActivitySequenceConflictError,
)
from mosemo.dependencies import ActivityServiceDep, CurrentAccountDep
from mosemo.exceptions import ApiException, ErrorCode
from mosemo.openapi import api_error_responses

router = APIRouter(prefix="/activities")


@router.post(
    "",
    operation_id="activitiesCreate",
    status_code=status.HTTP_201_CREATED,
    response_model=ActivityCreateResponse,
    summary="활동 레코드 생성",
    description=(
        "인증된 계정의 기기에서 관찰한 활동 또는 수집 상태 변경 한 건을 저장합니다. "
        "같은 eventId와 같은 내용의 재전송은 최초 저장 결과를 반환합니다."
    ),
    response_description="저장되었거나 동일한 재전송으로 확인된 활동 레코드입니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ErrorCode.REQUEST_ROUTE_NOT_FOUND,
        ErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
        ErrorCode.ACTIVITY_EVENT_ID_CONFLICT,
        ErrorCode.ACTIVITY_SEQUENCE_CONFLICT,
        ErrorCode.INVALID_ARGUMENT,
    ),
)
async def create_activity(
    record: ActivityRecord,
    service: ActivityServiceDep,
    account: CurrentAccountDep,
) -> ActivityCreateResponse:
    try:
        return await service.create_activity(
            account_id=account.account_id,
            record=record,
        )
    except ActivityDeviceNotFoundError as exc:
        raise ApiException(ErrorCode.ACTIVITY_DEVICE_NOT_FOUND) from exc
    except ActivityEventIdConflictError as exc:
        raise ApiException(ErrorCode.ACTIVITY_EVENT_ID_CONFLICT) from exc
    except ActivitySequenceConflictError as exc:
        raise ApiException(ErrorCode.ACTIVITY_SEQUENCE_CONFLICT) from exc

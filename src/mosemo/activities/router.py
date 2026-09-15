from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status

from mosemo.activities.schemas import (
    ActivityCreateResponse,
    ActivityRecord,
    TimelineSegmentResponse,
)
from mosemo.activities.service import (
    ActivityAccountNotFoundError,
    ActivityDeviceNotFoundError,
    ActivityEventIdConflictError,
    ActivitySequenceConflictError,
    ActivityTimelineBusyError,
)
from mosemo.dependencies import ActivityServiceDep, AuthenticatedAccountDep
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
        ErrorCode.ACTIVITY_TIMELINE_BUSY,
        headers={
            503: {
                "Retry-After": {
                    "description": "같은 eventId와 body로 재시도하기 전 대기할 초입니다.",
                    "schema": {"type": "string", "const": "1"},
                }
            }
        },
    ),
)
async def create_activity(
    record: ActivityRecord,
    service: ActivityServiceDep,
    authenticated_account: AuthenticatedAccountDep,
) -> ActivityCreateResponse:
    try:
        return await service.create_activity(
            account_id=authenticated_account.account_id,
            record=record,
        )
    except ActivityDeviceNotFoundError as exc:
        raise ApiException(ErrorCode.ACTIVITY_DEVICE_NOT_FOUND) from exc
    except ActivityEventIdConflictError as exc:
        raise ApiException(ErrorCode.ACTIVITY_EVENT_ID_CONFLICT) from exc
    except ActivitySequenceConflictError as exc:
        raise ApiException(ErrorCode.ACTIVITY_SEQUENCE_CONFLICT) from exc
    except ActivityTimelineBusyError as exc:
        raise ApiException(ErrorCode.ACTIVITY_TIMELINE_BUSY) from exc


@router.get(
    "/timeline",
    operation_id="activitiesGetTimeline",
    response_model=list[TimelineSegmentResponse],
    summary="날짜별 관찰 타임라인 조회",
    description=(
        "계정 시간대의 달력 날짜 하나에 해당하는 전체 관찰 타임라인을 "
        "날짜 경계에서 자르지 않은 구간 배열로 반환합니다."
    ),
    response_description="활동 구간과 명시적 수집 공백의 관찰 순서 배열입니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ErrorCode.INVALID_ARGUMENT,
    ),
)
async def get_timeline(
    service: ActivityServiceDep,
    authenticated_account: AuthenticatedAccountDep,
    date: Annotated[
        date,
        Query(description="계정 시간대 기준 조회할 달력 날짜(YYYY-MM-DD)입니다."),
    ],
) -> list[TimelineSegmentResponse]:
    try:
        return await service.get_timeline(
            account_id=authenticated_account.account_id,
            date=date,
        )
    except ActivityAccountNotFoundError as exc:
        raise ApiException(ErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc

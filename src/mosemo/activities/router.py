from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from mosemo.activities.messages import ActivityRegistrationRequested
from mosemo.activities.schemas import (
    ActivityBatchRequest,
    ActivityBatchResponse,
    TimelineSegmentResponse,
)
from mosemo.activities.service import ActivityAccountNotFoundError
from mosemo.dependencies import (
    ActivityServiceDep,
    AuthenticatedAccountDep,
    DeviceRepositoryDep,
)
from mosemo.exceptions import ApiException, ErrorCode
from mosemo.infra.batch_delivery import (
    BatchDelivery,
    DeliveryUnavailable,
)
from mosemo.openapi import api_error_responses

router = APIRouter(prefix="/activities")


@router.post(
    "/batches",
    operation_id="activitiesAcceptBatch",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ActivityBatchResponse,
    summary="활동 배치 전달 수락",
    description="인증 계정의 Device 원본 1~200건, 비압축 JSON 512 KiB 이하를 개발용 메모리 경로에 수락합니다. DB 저장 완료 또는 내구성 있는 Kafka ACK를 보장하지 않으며 종료 시 미처리 원본이 유실될 수 있습니다.",
    response_description="메모리 전달 수락 결과입니다. DB 반영은 타임라인 조회로 확인합니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
        ErrorCode.REQUEST_ROUTE_NOT_FOUND,
        ErrorCode.ACTIVITY_BATCH_TOO_LARGE,
        ErrorCode.INVALID_ARGUMENT,
        ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE,
        headers={
            503: {
                "Retry-After": {
                    "description": "같은 batchId와 내용으로 재시도하기 전 대기할 초입니다.",
                    "schema": {"type": "string", "const": "1"},
                }
            }
        },
    ),
)
async def accept_batch(
    batch: ActivityBatchRequest,
    request: Request,
    authenticated_account: AuthenticatedAccountDep,
    devices: DeviceRepositoryDep,
) -> ActivityBatchResponse:
    account_id = authenticated_account.account_id
    device = await devices.find_owned_by_id(
        account_id=account_id, device_id=batch.device_id
    )
    if device is None:
        raise ApiException(ErrorCode.ACTIVITY_DEVICE_NOT_FOUND)
    delivery: BatchDelivery | None = getattr(request.app.state, "batch_delivery", None)
    if delivery is None:
        raise ApiException(ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE)
    try:
        delivery.admit(
            ActivityRegistrationRequested(
                account_id, batch.device_id, batch.batch_id, await request.body()
            )
        )
    except DeliveryUnavailable as exc:
        raise ApiException(ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE) from exc
    return ActivityBatchResponse(batch_id=batch.batch_id, status="accepted")


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

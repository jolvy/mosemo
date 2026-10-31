from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from mosemo.activities.executor import LegacyBatchExecutor
from mosemo.activities.messages import TerminalBatchError
from mosemo.activities.schemas import (
    ActivityBatchProcessedResponse,
    ActivityBatchRecordResult,
    ActivityBatchRequest,
    ActivityBatchResponse,
    TimelineSegmentResponse,
)
from mosemo.activities.service import (
    ActivityAccountNotFoundError,
    ActivityTimelineBusyError,
)
from mosemo.dependencies import (
    ActivityServiceDep,
    AuthenticatedAccountDep,
    ClockDep,
    DeviceRepositoryDep,
    SessionDep,
)
from mosemo.exceptions import ApiException, ErrorCode
from mosemo.infra.batch_delivery import (
    ActivityBatchPublisher,
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
    description="인증 계정의 Device 원본 1~200건, 비압축 JSON 512 KiB 이하를 Kafka에 발행하고 발행 확인 후 수락합니다. 발행 확인은 최대 5초 대기하며 HTTP 전체 수락 제한은 10초입니다. 별도 Consumer의 DB 처리나 offset commit 완료를 기다리지 않습니다. 503 응답에는 Retry-After: 1을 반환합니다. 확인 timeout은 미발행을 뜻하지 않으므로 같은 batchId와 내용으로 재시도합니다.",
    response_description="Kafka 발행 ACK를 확인한 결과이며 원본별 처리 성공·DB commit·Consumer offset commit을 뜻하지 않습니다. DB 반영은 타임라인 조회로 확인합니다.",
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
    publisher: ActivityBatchPublisher | None = getattr(
        request.app.state, "batch_publisher", None
    )
    if publisher is None:
        raise ApiException(ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE)
    try:
        await publisher.publish(account_id, batch)
    except DeliveryUnavailable as exc:
        raise ApiException(ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE) from exc
    return ActivityBatchResponse(batch_id=batch.batch_id, status="accepted")


@router.post(
    "/batches/legacy",
    operation_id="activitiesProcessBatchLegacy",
    response_model=ActivityBatchProcessedResponse,
    summary="활동 배치 직접 처리",
    description="이벤트를 발행하지 않고 활동 배치를 직접 처리합니다. 원본 저장과 타임라인 구간 반영이 완료된 뒤 응답합니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
        ErrorCode.ACTIVITY_BATCH_CONFLICT,
        ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE,
        ErrorCode.ACTIVITY_BATCH_TOO_LARGE,
        ErrorCode.INVALID_ARGUMENT,
        ErrorCode.ACTIVITY_TIMELINE_BUSY,
        ErrorCode.FOCUS_SESSION_NOT_FOUND,
        ErrorCode.FOCUS_SESSION_CONFLICT,
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
async def process_batch_legacy(
    batch: ActivityBatchRequest,
    authenticated_account: AuthenticatedAccountDep,
    session: SessionDep,
    clock: ClockDep,
) -> ActivityBatchProcessedResponse:
    try:
        results = await LegacyBatchExecutor(session, clock=clock).execute(
            authenticated_account.account_id, batch
        )
    except ActivityTimelineBusyError as exc:
        raise ApiException(ErrorCode.ACTIVITY_TIMELINE_BUSY) from exc
    except TerminalBatchError as exc:
        error = {
            "device_not_owned": ErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
            "batch_content_conflict": ErrorCode.ACTIVITY_BATCH_CONFLICT,
            "projection_time_regression": ErrorCode.INVALID_ARGUMENT,
            "FocusSessionNotFoundError": ErrorCode.FOCUS_SESSION_NOT_FOUND,
            "FocusSessionInvalidTimeError": ErrorCode.FOCUS_SESSION_CONFLICT,
        }.get(str(exc))
        if error is None:
            raise
        raise ApiException(error) from exc
    return ActivityBatchProcessedResponse(
        batch_id=batch.batch_id,
        status="processed",
        results=[
            ActivityBatchRecordResult.model_validate(result) for result in results
        ],
    )


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

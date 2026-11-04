from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from mosemo.identity.public import AccountNotFoundError
from mosemo.observation.application.activities import GetObservationTimeline
from mosemo.observation.application.batches import (
    AcceptActivityBatch,
    ProcessLegacyActivityBatch,
    TerminalBatchError,
)
from mosemo.observation.application.ports import (
    ActivityBatchPublisher,
    ActivityDeliveryUnavailableError,
)
from mosemo.observation.domain.activities import ActivityDeviceNotFoundError
from mosemo.observation.domain.timeline import ActivityTimelineBusyError
from mosemo.observation.presentation.activities.mappers import (
    activity_batch,
    timeline_segment_response,
)
from mosemo.observation.presentation.activities.schemas import (
    ActivityBatchProcessedResponse,
    ActivityBatchRecordResult,
    ActivityBatchRequest,
    ActivityBatchResponse,
    TimelineSegmentResponse,
)
from mosemo.observation.presentation.dependencies import SessionDep
from mosemo.observation.presentation.errors import ObservationErrorCode
from mosemo.shared.errors import ApiErrorCode, ApiException, CommonErrorCode
from mosemo.shared.openapi import api_error_responses
from mosemo.shared.presentation.current_account import CurrentAccountDep

type BatchPublisherProvider = Callable[
    ..., ActivityBatchPublisher[ActivityBatchRequest] | None
]

TERMINAL_BATCH_ERRORS: dict[str, ApiErrorCode] = {
    "device_not_owned": ObservationErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
    "batch_content_conflict": ObservationErrorCode.ACTIVITY_BATCH_CONFLICT,
    "projection_time_regression": CommonErrorCode.INVALID_ARGUMENT,
    "FocusSessionNotFoundError": ObservationErrorCode.FOCUS_SESSION_NOT_FOUND,
    "FocusSessionInvalidTimeError": ObservationErrorCode.FOCUS_SESSION_CONFLICT,
}


def create_router(
    *,
    accept_activity_batch: AcceptActivityBatch,
    process_legacy_activity_batch: ProcessLegacyActivityBatch,
    get_observation_timeline: GetObservationTimeline,
    get_batch_publisher: BatchPublisherProvider,
) -> APIRouter:
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
            CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
            ObservationErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
            CommonErrorCode.REQUEST_ROUTE_NOT_FOUND,
            ObservationErrorCode.ACTIVITY_BATCH_TOO_LARGE,
            CommonErrorCode.INVALID_ARGUMENT,
            ObservationErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE,
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
        current_account: CurrentAccountDep,
        *,
        publisher: Annotated[
            ActivityBatchPublisher[ActivityBatchRequest] | None,
            Depends(get_batch_publisher),
        ],
        db_session: SessionDep,
    ) -> ActivityBatchResponse:
        try:
            await accept_activity_batch.execute(
                db_session,
                account_id=current_account.account_id,
                device_id=batch.device_id,
                batch=batch,
                publisher=publisher,
            )
        except ActivityDeviceNotFoundError as exc:
            raise ApiException(ObservationErrorCode.ACTIVITY_DEVICE_NOT_FOUND) from exc
        except ActivityDeliveryUnavailableError as exc:
            raise ApiException(
                ObservationErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE
            ) from exc
        return ActivityBatchResponse(batch_id=batch.batch_id, status="accepted")

    @router.post(
        "/batches/legacy",
        operation_id="activitiesProcessBatchLegacy",
        response_model=ActivityBatchProcessedResponse,
        summary="활동 배치 직접 처리",
        description="이벤트를 발행하지 않고 활동 배치를 직접 처리합니다. 원본 저장과 타임라인 구간 반영이 완료된 뒤 응답합니다.",
        responses=api_error_responses(
            CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
            ObservationErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
            ObservationErrorCode.ACTIVITY_BATCH_CONFLICT,
            ObservationErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE,
            ObservationErrorCode.ACTIVITY_BATCH_TOO_LARGE,
            CommonErrorCode.INVALID_ARGUMENT,
            ObservationErrorCode.ACTIVITY_TIMELINE_BUSY,
            ObservationErrorCode.FOCUS_SESSION_NOT_FOUND,
            ObservationErrorCode.FOCUS_SESSION_CONFLICT,
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
        current_account: CurrentAccountDep,
        *,
        db_session: SessionDep,
    ) -> ActivityBatchProcessedResponse:
        try:
            results = await process_legacy_activity_batch.execute(
                db_session,
                account_id=current_account.account_id,
                batch=activity_batch(batch),
            )
        except ActivityTimelineBusyError as exc:
            raise ApiException(ObservationErrorCode.ACTIVITY_TIMELINE_BUSY) from exc
        except TerminalBatchError as exc:
            error = TERMINAL_BATCH_ERRORS.get(str(exc))
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
            CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
            CommonErrorCode.INVALID_ARGUMENT,
        ),
    )
    async def get_timeline(
        current_account: CurrentAccountDep,
        db_session: SessionDep,
        *,
        date: Annotated[
            date,
            Query(description="계정 시간대 기준 조회할 달력 날짜(YYYY-MM-DD)입니다."),
        ],
    ) -> list[TimelineSegmentResponse]:
        try:
            segments = await get_observation_timeline.execute(
                db_session, account_id=current_account.account_id, day=date
            )
        except AccountNotFoundError as exc:
            raise ApiException(CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc
        return [timeline_segment_response(segment) for segment in segments]

    return router

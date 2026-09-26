from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from mosemo.activities.service import ActivityTimelineBusyError
from mosemo.dependencies import ActivityLabelServiceDep, AuthenticatedAccountDep
from mosemo.exceptions import ApiException, ErrorCode
from mosemo.labels.schemas import (
    ActivityLabelConfirmationRequest,
    ActivityLabelStateResponse,
    BatchLabelConfirmationRequest,
    BatchLabelConfirmationResponse,
    ConfirmedActivityLabelStateResponse,
    LabelTimelineItemResponse,
)
from mosemo.labels.service import (
    ActivityAccountNotFoundError,
    ActivityLabelSegmentChangedError,
    ActivityLabelSegmentNotFoundError,
    ActivityLabelSegmentNotLabelableError,
    ActivityLabelUnavailableError,
    BatchLabelConfirmationFailure,
)
from mosemo.openapi import api_error_responses
from mosemo.schemas import ValidationDetail

router = APIRouter(prefix="/activities")


def _batch_failure_response(exc: BatchLabelConfirmationFailure) -> ApiException:
    error_codes: dict[type[Exception], ErrorCode] = {
        ActivityLabelSegmentNotFoundError: ErrorCode.ACTIVITY_SEGMENT_NOT_FOUND,
        ActivityLabelSegmentNotLabelableError: ErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE,
        ActivityLabelSegmentChangedError: ErrorCode.ACTIVITY_SEGMENT_CHANGED,
        ActivityLabelUnavailableError: ErrorCode.LABEL_NOT_AVAILABLE,
    }
    error_code = error_codes[type(exc.reason)]
    location: list[str | int] = ["body", "items", exc.item_index]
    if exc.segment_index is not None:
        location.extend(["segments", exc.segment_index])
    elif isinstance(exc.reason, ActivityLabelSegmentChangedError):
        location.append("segments")
    return ApiException(
        error_code,
        details=[
            ValidationDetail(
                loc=location,
                msg=error_code.message,
                type=error_code.status.lower(),
            )
        ],
    )


@router.post(
    "/label-confirmations",
    operation_id="activitiesConfirmLabelGroups",
    response_model=BatchLabelConfirmationResponse,
    summary="라벨 묶음 일괄 확정",
    description=(
        "라벨 타임라인에서 조회한 하나 이상의 묶음을 각자의 선택으로 "
        "한 요청에서 확정합니다. 어느 항목이라도 유효하지 않으면 전체 요청을 적용하지 않습니다."
    ),
    response_description="요청한 순서대로 반환한 묶음의 최신 확정 상태입니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ErrorCode.ACTIVITY_SEGMENT_NOT_FOUND,
        ErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE,
        ErrorCode.ACTIVITY_SEGMENT_CHANGED,
        ErrorCode.LABEL_NOT_AVAILABLE,
        ErrorCode.ACTIVITY_TIMELINE_BUSY,
        ErrorCode.INVALID_ARGUMENT,
        headers={
            503: {
                "Retry-After": {
                    "description": "확정 요청을 재시도하기 전 대기할 초입니다.",
                    "schema": {"type": "string", "const": "1"},
                }
            }
        },
    ),
)
async def confirm_label_groups(
    request: BatchLabelConfirmationRequest,
    service: ActivityLabelServiceDep,
    authenticated_account: AuthenticatedAccountDep,
) -> BatchLabelConfirmationResponse:
    try:
        return await service.confirm_batch(
            account_id=authenticated_account.account_id,
            request=request,
        )
    except BatchLabelConfirmationFailure as exc:
        raise _batch_failure_response(exc) from exc
    except ActivityTimelineBusyError as exc:
        raise ApiException(ErrorCode.ACTIVITY_TIMELINE_BUSY) from exc


@router.get(
    "/label-timeline",
    operation_id="activitiesGetLabelTimeline",
    response_model=list[LabelTimelineItemResponse],
    summary="날짜별 라벨 타임라인 조회",
    description=(
        "계정 시간대의 날짜별 라벨 타임라인을 반환합니다. date를 생략하면 "
        "계정 시간대의 오늘을 조회하며, 시간상 연속이고 라벨과 확정 상태가 "
        "같은 닫힌 상세 활동 구간만 하나의 항목으로 묶습니다."
    ),
    response_description="라벨 활동 묶음, 진행 중 활동, 불투명 활동, 수집 공백 배열입니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ErrorCode.INVALID_ARGUMENT,
    ),
)
async def get_label_timeline(
    service: ActivityLabelServiceDep,
    authenticated_account: AuthenticatedAccountDep,
    date: Annotated[
        date | None,
        Query(
            description=(
                "계정 시간대 기준 조회할 달력 날짜(YYYY-MM-DD)입니다. "
                "생략하면 오늘을 조회합니다."
            ),
        ),
    ] = None,
) -> list[LabelTimelineItemResponse]:
    try:
        return await service.get_timeline(
            account_id=authenticated_account.account_id,
            date=date,
        )
    except ActivityAccountNotFoundError as exc:
        raise ApiException(ErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc


@router.get(
    "/segments/{segment_id}/label-state",
    operation_id="activitiesGetSegmentLabelState",
    response_model=ActivityLabelStateResponse,
    summary="관찰 구간 라벨 상태 조회",
    description=(
        "인증된 계정의 닫힌 상세 관찰 구간 하나에 저장된 AI 제안과 라벨 확정 "
        "상태를 구분해 조회합니다. segmentVersion은 이후 확정 요청의 대상 "
        "버전입니다. 실패하거나 처리 중인 제안도 확정 전에는 검토 대기입니다."
    ),
    response_description="관찰 구간의 제안 및 사용자 확정 상태입니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ErrorCode.ACTIVITY_SEGMENT_NOT_FOUND,
        ErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE,
        ErrorCode.INVALID_ARGUMENT,
    ),
)
async def get_segment_label_state(
    segment_id: Annotated[
        UUID,
        Path(description="라벨 상태를 조회할 관찰 구간 식별자입니다."),
    ],
    service: ActivityLabelServiceDep,
    authenticated_account: AuthenticatedAccountDep,
) -> ActivityLabelStateResponse:
    try:
        return await service.get_state(
            account_id=authenticated_account.account_id,
            segment_id=segment_id,
        )
    except ActivityLabelSegmentNotFoundError as exc:
        raise ApiException(ErrorCode.ACTIVITY_SEGMENT_NOT_FOUND) from exc
    except ActivityLabelSegmentNotLabelableError as exc:
        raise ApiException(ErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE) from exc


@router.put(
    "/segments/{segment_id}/label-confirmation",
    operation_id="activitiesPutSegmentLabelConfirmation",
    status_code=status.HTTP_200_OK,
    response_model=ConfirmedActivityLabelStateResponse,
    summary="관찰 구간 라벨 확정",
    description=(
        "인증된 계정의 닫힌 상세 관찰 구간 하나를 활성 라벨 또는 미분류로 "
        "확정하거나 최신 선택으로 정정합니다."
    ),
    response_description="저장된 최신 라벨 확정 상태입니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ErrorCode.ACTIVITY_SEGMENT_NOT_FOUND,
        ErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE,
        ErrorCode.ACTIVITY_SEGMENT_CHANGED,
        ErrorCode.LABEL_NOT_AVAILABLE,
        ErrorCode.ACTIVITY_TIMELINE_BUSY,
        ErrorCode.INVALID_ARGUMENT,
        headers={
            503: {
                "Retry-After": {
                    "description": "확정 요청을 재시도하기 전 대기할 초입니다.",
                    "schema": {"type": "string", "const": "1"},
                }
            }
        },
    ),
)
async def put_segment_label_confirmation(
    segment_id: Annotated[
        UUID,
        Path(description="라벨을 확정할 관찰 구간 식별자입니다."),
    ],
    request: ActivityLabelConfirmationRequest,
    service: ActivityLabelServiceDep,
    authenticated_account: AuthenticatedAccountDep,
) -> ConfirmedActivityLabelStateResponse:
    try:
        return await service.confirm(
            account_id=authenticated_account.account_id,
            segment_id=segment_id,
            request=request,
        )
    except ActivityLabelSegmentNotFoundError as exc:
        raise ApiException(ErrorCode.ACTIVITY_SEGMENT_NOT_FOUND) from exc
    except ActivityLabelSegmentNotLabelableError as exc:
        raise ApiException(ErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE) from exc
    except ActivityLabelSegmentChangedError as exc:
        raise ApiException(ErrorCode.ACTIVITY_SEGMENT_CHANGED) from exc
    except ActivityLabelUnavailableError as exc:
        raise ApiException(ErrorCode.LABEL_NOT_AVAILABLE) from exc
    except ActivityTimelineBusyError as exc:
        raise ApiException(ErrorCode.ACTIVITY_TIMELINE_BUSY) from exc

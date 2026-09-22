from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, status

from mosemo.activities.service import ActivityTimelineBusyError
from mosemo.dependencies import ActivityLabelServiceDep, AuthenticatedAccountDep
from mosemo.exceptions import ApiException, ErrorCode
from mosemo.labels.schemas import (
    ActivityLabelConfirmationRequest,
    ActivityLabelStateResponse,
    ConfirmedActivityLabelStateResponse,
)
from mosemo.labels.service import (
    ActivityLabelSegmentChangedError,
    ActivityLabelSegmentNotFoundError,
    ActivityLabelSegmentNotLabelableError,
    ActivityLabelUnavailableError,
)
from mosemo.openapi import api_error_responses

router = APIRouter(prefix="/activities/segments")


@router.get(
    "/{segment_id}/label-state",
    operation_id="activitiesGetSegmentLabelState",
    response_model=ActivityLabelStateResponse,
    summary="관찰 구간 라벨 상태 조회",
    description=(
        "인증된 계정의 닫힌 상세 관찰 구간 하나에 저장된 라벨 확정 상태를 "
        "조회합니다. segmentVersion은 이후 확정 요청의 대상 버전입니다."
    ),
    response_description="관찰 구간의 미확정 또는 확정 라벨 상태입니다.",
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
    "/{segment_id}/label-confirmation",
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

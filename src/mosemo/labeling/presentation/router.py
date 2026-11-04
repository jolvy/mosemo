from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from mosemo.identity.public import AccountNotFoundError
from mosemo.labeling.application.confirmations import (
    BatchLabelConfirmationError,
    ConfirmSegmentLabel,
    ConfirmSegmentLabels,
    SelectionField,
)
from mosemo.labeling.application.states import GetSegmentLabelState
from mosemo.labeling.application.timeline import GetLabelTimeline
from mosemo.labeling.domain.confirmations import (
    ActivityLabelConfirmationConflictError,
    ActivityLabelSegmentChangedError,
    ActivityLabelSegmentDuplicateError,
    ActivityLabelSegmentNotFoundError,
    ActivityLabelSegmentNotLabelableError,
    ActivityLabelUnavailableError,
)
from mosemo.labeling.presentation.dependencies import SessionDep
from mosemo.labeling.presentation.errors import LabelingErrorCode
from mosemo.labeling.presentation.mappers import (
    LabelSelectionMapper,
    LabelStateMapper,
    LabelTimelineMapper,
)
from mosemo.labeling.presentation.schemas import (
    ActivityLabelConfirmationRequest,
    ActivityLabelStateResponse,
    BatchLabelConfirmationRequest,
    BatchLabelConfirmationResponse,
    ConfirmedActivityLabelStateResponse,
    LabelTimelineItemResponse,
)
from mosemo.labels.public import LabelErrorCode
from mosemo.observation.public import ActivityTimelineBusyError, ObservationErrorCode
from mosemo.shared.errors import ApiErrorCode, ApiException, CommonErrorCode
from mosemo.shared.openapi import api_error_responses
from mosemo.shared.presentation.current_account import CurrentAccountDep
from mosemo.shared.presentation.schemas import ValidationDetail

ERROR_CODES: dict[type[Exception], ApiErrorCode] = {
    ActivityLabelSegmentNotFoundError: LabelingErrorCode.ACTIVITY_SEGMENT_NOT_FOUND,
    ActivityLabelSegmentNotLabelableError: (
        LabelingErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE
    ),
    ActivityLabelSegmentChangedError: LabelingErrorCode.ACTIVITY_SEGMENT_CHANGED,
    ActivityLabelConfirmationConflictError: (
        LabelingErrorCode.ACTIVITY_LABEL_CONFIRMATION_CONFLICT
    ),
    ActivityLabelSegmentDuplicateError: CommonErrorCode.INVALID_ARGUMENT,
    ActivityLabelUnavailableError: LabelErrorCode.LABEL_NOT_AVAILABLE,
    ActivityTimelineBusyError: ObservationErrorCode.ACTIVITY_TIMELINE_BUSY,
    AccountNotFoundError: CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
}
FIELD_NAMES: dict[SelectionField, str] = {
    "segment_id": "segmentId",
    "segment_version": "segmentVersion",
    "selection": "selection",
}
RETRY_AFTER_HEADERS = {
    503: {
        "Retry-After": {
            "description": "확정 요청을 재시도하기 전 대기할 초입니다.",
            "schema": {"type": "string", "const": "1"},
        }
    }
}


def _batch_failure_response(exc: BatchLabelConfirmationError) -> ApiException:
    error_code = ERROR_CODES[type(exc.reason)]
    return ApiException(
        error_code,
        details=[
            ValidationDetail(
                loc=["body", "items", exc.item_index, FIELD_NAMES[exc.field_name]],
                msg=error_code.message,
                type=error_code.status.lower(),
            )
        ],
    )


@contextmanager
def api_errors() -> Iterator[None]:
    """Translate the labeling use case errors to public error codes."""
    try:
        yield
    except BatchLabelConfirmationError as exc:
        raise _batch_failure_response(exc) from exc
    except tuple(ERROR_CODES) as exc:
        raise ApiException(ERROR_CODES[type(exc)]) from exc


def create_router(
    *,
    confirm_segment_label: ConfirmSegmentLabel,
    confirm_segment_labels: ConfirmSegmentLabels,
    get_label_timeline: GetLabelTimeline,
    get_segment_label_state: GetSegmentLabelState,
) -> APIRouter:
    router = APIRouter(prefix="/activities")
    selections = LabelSelectionMapper()
    states = LabelStateMapper()
    timeline = LabelTimelineMapper()

    @router.post(
        "/label-confirmations",
        operation_id="activitiesConfirmSegmentLabels",
        response_model=BatchLabelConfirmationResponse,
        summary="관찰 구간 라벨 일괄 확정",
        description=(
            "라벨 타임라인 그룹과 관계없이 닫힌 상세 관찰 구간마다 선택을 "
            "지정해 한 요청에서 확정합니다. 모든 항목이 유효해야 전체 요청을 적용하며, "
            "같은 관찰 버전에 이미 다른 선택이 확정되어 있으면 충돌을 반환합니다."
        ),
        response_description="요청한 구간 순서대로 반환한 최신 확정 상태입니다.",
        responses=api_error_responses(
            CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
            LabelingErrorCode.ACTIVITY_SEGMENT_NOT_FOUND,
            LabelingErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE,
            LabelingErrorCode.ACTIVITY_SEGMENT_CHANGED,
            LabelingErrorCode.ACTIVITY_LABEL_CONFIRMATION_CONFLICT,
            LabelErrorCode.LABEL_NOT_AVAILABLE,
            ObservationErrorCode.ACTIVITY_TIMELINE_BUSY,
            CommonErrorCode.INVALID_ARGUMENT,
            headers=RETRY_AFTER_HEADERS,
        ),
    )
    async def confirm_segment_labels_route(
        request: BatchLabelConfirmationRequest,
        db_session: SessionDep,
        *,
        account: CurrentAccountDep,
    ) -> BatchLabelConfirmationResponse:
        with api_errors():
            saved = await confirm_segment_labels.execute(
                db_session,
                account_id=account.account_id,
                selections=selections.to_selections(request),
            )
        return BatchLabelConfirmationResponse(
            items=[states.to_confirmed_response(state) for state in saved]
        )

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
            CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
            CommonErrorCode.INVALID_ARGUMENT,
        ),
    )
    async def get_label_timeline_route(
        db_session: SessionDep,
        account: CurrentAccountDep,
        *,
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
        with api_errors():
            items = await get_label_timeline.execute(
                db_session, account_id=account.account_id, day=date
            )
        return [timeline.to_response(item) for item in items]

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
            CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
            LabelingErrorCode.ACTIVITY_SEGMENT_NOT_FOUND,
            LabelingErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE,
            CommonErrorCode.INVALID_ARGUMENT,
        ),
    )
    async def get_segment_label_state_route(
        segment_id: Annotated[
            UUID,
            Path(description="라벨 상태를 조회할 관찰 구간 식별자입니다."),
        ],
        db_session: SessionDep,
        *,
        account: CurrentAccountDep,
    ) -> ActivityLabelStateResponse:
        with api_errors():
            state = await get_segment_label_state.execute(
                db_session, account_id=account.account_id, segment_id=segment_id
            )
        return states.to_response(state)

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
            CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
            LabelingErrorCode.ACTIVITY_SEGMENT_NOT_FOUND,
            LabelingErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE,
            LabelingErrorCode.ACTIVITY_SEGMENT_CHANGED,
            LabelErrorCode.LABEL_NOT_AVAILABLE,
            ObservationErrorCode.ACTIVITY_TIMELINE_BUSY,
            CommonErrorCode.INVALID_ARGUMENT,
            headers=RETRY_AFTER_HEADERS,
        ),
    )
    async def put_segment_label_confirmation(
        segment_id: Annotated[
            UUID,
            Path(description="라벨을 확정할 관찰 구간 식별자입니다."),
        ],
        request: ActivityLabelConfirmationRequest,
        *,
        db_session: SessionDep,
        account: CurrentAccountDep,
    ) -> ConfirmedActivityLabelStateResponse:
        with api_errors():
            state = await confirm_segment_label.execute(
                db_session,
                account_id=account.account_id,
                selection=selections.to_selection(request, segment_id=segment_id),
            )
        return states.to_confirmed_response(state)

    return router

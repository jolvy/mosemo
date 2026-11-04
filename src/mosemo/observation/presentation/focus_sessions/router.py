from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from mosemo.identity.public import AccountNotFoundError
from mosemo.labels.public import LabelErrorCode
from mosemo.observation.application.focus_sessions import (
    CompleteFocusSession,
    FocusSessionLabelNotAvailableError,
    ListCompletedFocusSessions,
    StartFocusSession,
)
from mosemo.observation.domain.activities import ActivityDeviceNotFoundError
from mosemo.observation.domain.focus_sessions import (
    FocusSessionConflictError,
    FocusSessionInvalidTimeError,
    FocusSessionNotFoundError,
)
from mosemo.observation.presentation.dependencies import SessionDep
from mosemo.observation.presentation.errors import ObservationErrorCode
from mosemo.observation.presentation.focus_sessions.mappers import (
    FocusSessionMapper,
)
from mosemo.observation.presentation.focus_sessions.schemas import (
    FocusSessionCompleteRequest,
    FocusSessionCreateRequest,
    FocusSessionResponse,
)
from mosemo.shared.errors import ApiErrorCode, ApiException, CommonErrorCode
from mosemo.shared.openapi import api_error_responses
from mosemo.shared.presentation.current_account import CurrentAccountDep

ERRORS = api_error_responses(
    CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
    ObservationErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
    ObservationErrorCode.FOCUS_SESSION_NOT_FOUND,
    ObservationErrorCode.FOCUS_SESSION_CONFLICT,
    LabelErrorCode.LABEL_NOT_AVAILABLE,
    CommonErrorCode.INVALID_ARGUMENT,
)


ERROR_CODES: dict[type[Exception], ApiErrorCode] = {
    ActivityDeviceNotFoundError: ObservationErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
    FocusSessionNotFoundError: ObservationErrorCode.FOCUS_SESSION_NOT_FOUND,
    FocusSessionConflictError: ObservationErrorCode.FOCUS_SESSION_CONFLICT,
    FocusSessionInvalidTimeError: CommonErrorCode.INVALID_ARGUMENT,
    FocusSessionLabelNotAvailableError: LabelErrorCode.LABEL_NOT_AVAILABLE,
    AccountNotFoundError: CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
}


@contextmanager
def api_errors() -> Iterator[None]:
    """Translate the focus session use case errors to public error codes."""
    try:
        yield
    except tuple(ERROR_CODES) as exc:
        raise ApiException(ERROR_CODES[type(exc)]) from exc


def create_router(
    *,
    start_focus_session: StartFocusSession,
    complete_focus_session: CompleteFocusSession,
    list_completed_focus_sessions: ListCompletedFocusSessions,
) -> APIRouter:
    router = APIRouter(prefix="/focus-sessions")
    mapper = FocusSessionMapper()

    @router.post(
        "",
        operation_id="focusSessionsCreate",
        status_code=201,
        response_model=FocusSessionResponse,
        summary="집중 세션 시작",
        description="활동 업로드 전에 세션을 생성합니다. 같은 세션 ID와 내용의 재시도는 같은 결과를 반환합니다.",
        responses=ERRORS,
    )
    async def create_focus_session(
        request: FocusSessionCreateRequest,
        db_session: SessionDep,
        *,
        account: CurrentAccountDep,
    ) -> FocusSessionResponse:
        with api_errors():
            stored = await start_focus_session.execute(
                db_session, mapper.to_entity(request, account_id=account.account_id)
            )
        return FocusSessionResponse.from_session(stored)

    @router.put(
        "/{session_id}/completion",
        operation_id="focusSessionsComplete",
        response_model=FocusSessionResponse,
        summary="종료한 집중 세션 기록 완료",
        description="라벨은 필수이고 설명은 선택입니다. 동일한 완료 요청은 재시도할 수 있고 확정된 기록은 변경할 수 없습니다.",
        responses=ERRORS,
    )
    async def complete_focus_session_route(
        session_id: Annotated[UUID, Path(description="집중 세션 식별자입니다.")],
        request: FocusSessionCompleteRequest,
        *,
        db_session: SessionDep,
        account: CurrentAccountDep,
    ) -> FocusSessionResponse:
        with api_errors():
            stored = await complete_focus_session.execute(
                db_session,
                account_id=account.account_id,
                focus_session_id=session_id,
                completion=mapper.to_completion(request),
            )
        return FocusSessionResponse.from_session(stored)

    @router.get(
        "",
        operation_id="focusSessionsList",
        response_model=list[FocusSessionResponse],
        summary="날짜별 완료한 집중 세션 조회",
        description="계정 시간대의 시작 날짜 기준, 최신순으로 완료한 세션만 반환합니다.",
        responses=ERRORS,
    )
    async def list_focus_sessions(
        db_session: SessionDep,
        account: CurrentAccountDep,
        *,
        date: Annotated[
            date, Query(description="계정 시간대 기준 세션 시작 날짜입니다.")
        ],
    ) -> list[FocusSessionResponse]:
        with api_errors():
            sessions = await list_completed_focus_sessions.execute(
                db_session, account_id=account.account_id, day=date
            )
        return [FocusSessionResponse.from_session(item) for item in sessions]

    return router

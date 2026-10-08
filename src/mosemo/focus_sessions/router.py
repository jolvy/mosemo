from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from mosemo.activities.repository import ActivityRepository
from mosemo.activities.service import (
    ActivityAccountNotFoundError,
    ActivityDeviceNotFoundError,
)
from mosemo.dependencies import AuthenticatedAccountDep, SessionDep
from mosemo.devices.repository import DeviceRepository
from mosemo.exceptions import ApiException, ErrorCode
from mosemo.focus_sessions.models import (
    FocusSessionConflictError,
    FocusSessionInvalidTimeError,
    FocusSessionNotFoundError,
)
from mosemo.focus_sessions.repository import FocusSessionRepository
from mosemo.focus_sessions.schemas import (
    FocusSessionCompleteRequest,
    FocusSessionCreateRequest,
    FocusSessionResponse,
)
from mosemo.focus_sessions.service import (
    FocusSessionLabelNotAvailableError,
    FocusSessionService,
)
from mosemo.openapi import api_error_responses

router = APIRouter(prefix="/focus-sessions")


def get_focus_session_service(session: SessionDep) -> FocusSessionService:
    return FocusSessionService(
        session=session,
        repository=FocusSessionRepository(session),
        activity_repository=ActivityRepository(session),
        device_repository=DeviceRepository(session),
    )


FocusSessionServiceDep = Annotated[
    FocusSessionService, Depends(get_focus_session_service)
]
ERRORS = api_error_responses(
    ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
    ErrorCode.ACTIVITY_DEVICE_NOT_FOUND,
    ErrorCode.FOCUS_SESSION_NOT_FOUND,
    ErrorCode.FOCUS_SESSION_CONFLICT,
    ErrorCode.LABEL_NOT_AVAILABLE,
    ErrorCode.INVALID_ARGUMENT,
)


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
    service: FocusSessionServiceDep,
    account: AuthenticatedAccountDep,
) -> FocusSessionResponse:
    try:
        return await service.create(account.account_id, request)
    except ActivityDeviceNotFoundError as exc:
        raise ApiException(ErrorCode.ACTIVITY_DEVICE_NOT_FOUND) from exc
    except FocusSessionConflictError as exc:
        raise ApiException(ErrorCode.FOCUS_SESSION_CONFLICT) from exc


@router.put(
    "/{session_id}/completion",
    operation_id="focusSessionsComplete",
    response_model=FocusSessionResponse,
    summary="종료한 집중 세션 기록 완료",
    description="라벨은 필수이고 설명은 선택입니다. 동일한 완료 요청은 재시도할 수 있고 확정된 기록은 변경할 수 없습니다.",
    responses=ERRORS,
)
async def complete_focus_session(
    session_id: Annotated[UUID, Path(description="집중 세션 식별자입니다.")],
    request: FocusSessionCompleteRequest,
    service: FocusSessionServiceDep,
    account: AuthenticatedAccountDep,
) -> FocusSessionResponse:
    try:
        return await service.complete(account.account_id, session_id, request)
    except FocusSessionNotFoundError as exc:
        raise ApiException(ErrorCode.FOCUS_SESSION_NOT_FOUND) from exc
    except FocusSessionConflictError as exc:
        raise ApiException(ErrorCode.FOCUS_SESSION_CONFLICT) from exc
    except FocusSessionInvalidTimeError as exc:
        raise ApiException(ErrorCode.INVALID_ARGUMENT) from exc
    except FocusSessionLabelNotAvailableError as exc:
        raise ApiException(ErrorCode.LABEL_NOT_AVAILABLE) from exc


@router.get(
    "",
    operation_id="focusSessionsList",
    response_model=list[FocusSessionResponse],
    summary="날짜별 완료한 집중 세션 조회",
    description="계정 시간대의 시작 날짜 기준, 최신순으로 완료한 세션만 반환합니다.",
    responses=ERRORS,
)
async def list_focus_sessions(
    service: FocusSessionServiceDep,
    account: AuthenticatedAccountDep,
    date: Annotated[date, Query(description="계정 시간대 기준 세션 시작 날짜입니다.")],
) -> list[FocusSessionResponse]:
    try:
        return await service.list_completed(account.account_id, date)
    except ActivityAccountNotFoundError as exc:
        raise ApiException(ErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc

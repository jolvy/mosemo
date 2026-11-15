from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response

from mosemo.analytics.application.reads import (
    AnalyticsFocusSessionInProgressError,
    AnalyticsFocusSessionNotFoundError,
    GetDailyStats,
    GetFocusSessionAnalysis,
)
from mosemo.analytics.presentation.dependencies import SessionDep
from mosemo.analytics.presentation.errors import AnalyticsErrorCode
from mosemo.analytics.presentation.mappers import AnalyticsMapper
from mosemo.analytics.presentation.schemas import (
    DailyStatsResponse,
    FocusSessionAnalysisResponse,
)
from mosemo.identity.public import AccountNotFoundError
from mosemo.shared.errors import ApiErrorCode, ApiException, CommonErrorCode
from mosemo.shared.openapi import api_error_responses
from mosemo.shared.presentation.current_account import CurrentAccountDep

ERROR_CODES: dict[type[Exception], ApiErrorCode] = {
    AnalyticsFocusSessionNotFoundError: (
        AnalyticsErrorCode.ANALYTICS_FOCUS_SESSION_NOT_FOUND
    ),
    AnalyticsFocusSessionInProgressError: (
        AnalyticsErrorCode.ANALYTICS_FOCUS_SESSION_IN_PROGRESS
    ),
    AccountNotFoundError: CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
}
NO_STORE: dict[int | str, dict[str, Any]] = {
    200: {
        "headers": {
            "Cache-Control": {
                "description": "응답을 저장하지 않도록 no-store로 설정됩니다.",
                "schema": {"type": "string", "const": "no-store"},
            }
        }
    }
}
DAILY_STATS_RESPONSES = {
    **NO_STORE,
    **api_error_responses(
        CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        CommonErrorCode.INVALID_ARGUMENT,
    ),
}
FOCUS_SESSION_RESPONSES = {
    **NO_STORE,
    **api_error_responses(
        CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        AnalyticsErrorCode.ANALYTICS_FOCUS_SESSION_NOT_FOUND,
        AnalyticsErrorCode.ANALYTICS_FOCUS_SESSION_IN_PROGRESS,
        CommonErrorCode.INVALID_ARGUMENT,
    ),
}


@contextmanager
def api_errors() -> Iterator[None]:
    """Translate the analytics read errors to public error codes."""
    try:
        yield
    except tuple(ERROR_CODES) as exc:
        raise ApiException(ERROR_CODES[type(exc)]) from exc


def create_router(
    *,
    get_daily_stats: GetDailyStats,
    get_focus_session_analysis: GetFocusSessionAnalysis,
) -> APIRouter:
    router = APIRouter()
    mapper = AnalyticsMapper()

    @router.get(
        "/daily-stats",
        operation_id="analyticsDailyStatsGet",
        response_model=DailyStatsResponse,
        summary="날짜별 일간 통계 조회",
        description=(
            "계정 시간대 날짜 하나의 앱·사이트 사용 시간, 전환, 라벨 전환, 수집 상태, "
            "그날 시작한 완료 세션의 합을 반환합니다. 지난 날짜는 최신 저장 결과가 "
            "있으면 그 값을, 아니면 원천에서 계산한 값을 반환하고 미래 날짜는 모두 0입니다. "
            "계산에 실패하면 500을 반환합니다."
        ),
        responses=DAILY_STATS_RESPONSES,
    )
    async def get_daily_stats_route(
        response: Response,
        db_session: SessionDep,
        *,
        account: CurrentAccountDep,
        date: Annotated[date, Query(description="계정 시간대 기준 날짜입니다.")],
    ) -> DailyStatsResponse:
        with api_errors():
            view = await get_daily_stats.execute(
                db_session, account_id=account.account_id, day=date
            )
        response.headers["Cache-Control"] = "no-store"
        return mapper.to_daily_stats_response(view)

    @router.get(
        "/focus-sessions/{focus_session_id}",
        operation_id="analyticsFocusSessionGet",
        response_model=FocusSessionAnalysisResponse,
        summary="완료한 집중 세션 분석 조회",
        description=(
            "완료한 세션 하나의 전환, 시간 구성, 이탈 대상, 이탈 근거, 라벨 전환을 "
            "조회할 때 계산해 반환합니다. 진행 중인 세션은 409를 반환합니다."
        ),
        responses=FOCUS_SESSION_RESPONSES,
    )
    async def get_focus_session_analysis_route(
        focus_session_id: Annotated[UUID, Path(description="집중 세션 식별자입니다.")],
        response: Response,
        *,
        db_session: SessionDep,
        account: CurrentAccountDep,
    ) -> FocusSessionAnalysisResponse:
        with api_errors():
            view = await get_focus_session_analysis.execute(
                db_session,
                account_id=account.account_id,
                focus_session_id=focus_session_id,
            )
        response.headers["Cache-Control"] = "no-store"
        return mapper.to_focus_session_response(view)

    return router

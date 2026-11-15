from collections.abc import Mapping

from mosemo.analytics.presentation.errors import AnalyticsErrorCode
from mosemo.shared.errors import ApiErrorCode
from mosemo.shared.openapi import ErrorDocs

ERROR_DOCS: Mapping[ApiErrorCode, ErrorDocs] = {
    AnalyticsErrorCode.ANALYTICS_FOCUS_SESSION_NOT_FOUND: ErrorDocs(
        summary="Focus session not found",
        description="집중 세션을 찾을 수 없거나 인증된 계정에 속하지 않습니다.",
    ),
    AnalyticsErrorCode.ANALYTICS_FOCUS_SESSION_IN_PROGRESS: ErrorDocs(
        summary="Focus session in progress",
        description="아직 완료하지 않은 집중 세션은 분석할 수 없습니다.",
    ),
}

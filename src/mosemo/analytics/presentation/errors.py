from mosemo.shared.errors import ApiErrorCode, ErrorSpec


class AnalyticsErrorCode(ApiErrorCode):
    ANALYTICS_FOCUS_SESSION_NOT_FOUND = ErrorSpec(
        code=404, message="Focus session not found"
    )
    ANALYTICS_FOCUS_SESSION_IN_PROGRESS = ErrorSpec(
        code=409, message="Focus session is still in progress"
    )

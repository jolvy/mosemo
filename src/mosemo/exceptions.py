from mosemo.shared.errors import ApiErrorCode, ErrorSpec


class ErrorCode(ApiErrorCode):
    """Central error codes kept until each bounded context moves its own members."""

    ACTIVITY_SEGMENT_NOT_FOUND = ErrorSpec(
        code=404,
        message="Activity segment not found",
    )
    ACTIVITY_SEGMENT_NOT_LABELABLE = ErrorSpec(
        code=409,
        message="Activity segment is not labelable",
    )
    ACTIVITY_SEGMENT_CHANGED = ErrorSpec(
        code=409,
        message="Activity segment changed; refresh before confirming",
    )
    ACTIVITY_LABEL_CONFIRMATION_CONFLICT = ErrorSpec(
        code=409,
        message="Activity label confirmation conflicts with an existing selection",
    )

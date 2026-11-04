from mosemo.shared.errors import ApiErrorCode, ErrorSpec


class ErrorCode(ApiErrorCode):
    """Central error codes kept until each bounded context moves its own members."""

    FOCUS_SESSION_NOT_FOUND = ErrorSpec(code=404, message="Focus session not found")
    FOCUS_SESSION_CONFLICT = ErrorSpec(
        code=409, message="Focus session conflicts with stored data"
    )
    ACTIVITY_DEVICE_NOT_FOUND = ErrorSpec(
        code=404,
        message="Activity device not found",
    )
    ACTIVITY_EVENT_ID_CONFLICT = ErrorSpec(
        code=409,
        message="Activity event ID conflicts with a stored record",
    )
    ACTIVITY_SEQUENCE_CONFLICT = ErrorSpec(
        code=409,
        message="Activity sequence conflicts with a stored record",
    )
    ACTIVITY_BATCH_CONFLICT = ErrorSpec(
        code=409, message="Activity batch ID conflicts with stored content"
    )
    ACTIVITY_BATCH_TOO_LARGE = ErrorSpec(
        code=413, message="Activity batch body is too large"
    )
    ACTIVITY_DELIVERY_UNAVAILABLE = ErrorSpec(
        code=503, message="Activity delivery is unavailable"
    )
    ACTIVITY_TIMELINE_BUSY = ErrorSpec(
        code=503,
        message="Activity timeline is busy",
    )
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

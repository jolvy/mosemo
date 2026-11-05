from mosemo.shared.errors import ApiErrorCode, ErrorSpec


class LabelingErrorCode(ApiErrorCode):
    LABELING_SEGMENT_NOT_FOUND = ErrorSpec(
        code=404,
        message="Activity segment not found",
    )
    LABELING_SEGMENT_NOT_LABELABLE = ErrorSpec(
        code=409,
        message="Activity segment is not labelable",
    )
    LABELING_SEGMENT_CHANGED = ErrorSpec(
        code=409,
        message="Activity segment changed; refresh before confirming",
    )
    LABELING_CONFIRMATION_CONFLICT = ErrorSpec(
        code=409,
        message="Activity label confirmation conflicts with an existing selection",
    )

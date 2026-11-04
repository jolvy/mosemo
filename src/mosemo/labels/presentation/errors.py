from mosemo.shared.errors import ApiErrorCode, ErrorSpec


class LabelErrorCode(ApiErrorCode):
    LABEL_NOT_AVAILABLE = ErrorSpec(
        code=404,
        message="Label not found or inactive",
    )

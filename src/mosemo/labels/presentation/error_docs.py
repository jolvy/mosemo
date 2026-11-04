from collections.abc import Mapping

from mosemo.labels.presentation.errors import LabelErrorCode
from mosemo.shared.errors import ApiErrorCode
from mosemo.shared.openapi import ErrorDocs

ERROR_DOCS: Mapping[ApiErrorCode, ErrorDocs] = {
    LabelErrorCode.LABEL_NOT_AVAILABLE: ErrorDocs(
        summary="Label not available",
        description="라벨을 찾을 수 없거나 현재 선택할 수 없는 상태입니다.",
    ),
}

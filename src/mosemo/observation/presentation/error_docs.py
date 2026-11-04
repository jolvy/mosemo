from collections.abc import Mapping

from mosemo.observation.presentation.errors import ObservationErrorCode
from mosemo.shared.errors import ApiErrorCode
from mosemo.shared.openapi import ErrorDocs

ERROR_DOCS: Mapping[ApiErrorCode, ErrorDocs] = {
    ObservationErrorCode.FOCUS_SESSION_NOT_FOUND: ErrorDocs(
        summary="Focus session not found",
        description="집중 세션을 찾을 수 없거나 요청 계정 또는 기기에 속하지 않습니다.",
    ),
    ObservationErrorCode.FOCUS_SESSION_CONFLICT: ErrorDocs(
        summary="Focus session conflict",
        description="동일한 세션 식별자로 저장한 요청 내용과 다릅니다.",
    ),
    ObservationErrorCode.ACTIVITY_DEVICE_NOT_FOUND: ErrorDocs(
        summary="Activity device not found",
        description=("Device를 찾을 수 없거나 인증된 계정에 속하지 않습니다."),
    ),
    ObservationErrorCode.ACTIVITY_EVENT_ID_CONFLICT: ErrorDocs(
        summary="Activity event ID conflict",
        description="같은 eventId로 저장된 활동 레코드와 요청 내용이 다릅니다.",
    ),
    ObservationErrorCode.ACTIVITY_SEQUENCE_CONFLICT: ErrorDocs(
        summary="Activity sequence conflict",
        description=(
            "같은 Device와 sequence로 저장된 이벤트가 요청 eventId와 다릅니다."
        ),
    ),
    ObservationErrorCode.ACTIVITY_BATCH_CONFLICT: ErrorDocs(
        summary="Activity batch conflict",
        description="같은 계정·Device·batchId로 처리한 배치의 내용과 다릅니다.",
    ),
    ObservationErrorCode.ACTIVITY_BATCH_TOO_LARGE: ErrorDocs(
        summary="Activity batch too large",
        description="비압축 UTF-8 JSON 본문은 512 KiB 이하입니다.",
    ),
    ObservationErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE: ErrorDocs(
        summary="Activity delivery unavailable",
        description="활동 배치를 수락할 수 없습니다. 같은 batchId와 내용으로 재시도합니다.",
    ),
    ObservationErrorCode.ACTIVITY_TIMELINE_BUSY: ErrorDocs(
        summary="Activity timeline busy",
        description="계정별 관찰 쓰기 잠금을 3초 안에 얻지 못했습니다.",
    ),
}

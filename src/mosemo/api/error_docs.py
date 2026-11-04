from collections.abc import Mapping

from mosemo.exceptions import ErrorCode
from mosemo.shared.errors import ApiErrorCode
from mosemo.shared.openapi import ErrorDocs
from mosemo.shared.presentation.schemas import ValidationDetail

# Temporary home of the central ErrorCode docs. Each bounded context move takes
# its members to that context's presentation/error_docs.py.
ERROR_DOCS: Mapping[ApiErrorCode, ErrorDocs] = {
    ErrorCode.FOCUS_SESSION_NOT_FOUND: ErrorDocs(
        summary="Focus session not found",
        description="집중 세션을 찾을 수 없거나 요청 계정 또는 기기에 속하지 않습니다.",
    ),
    ErrorCode.FOCUS_SESSION_CONFLICT: ErrorDocs(
        summary="Focus session conflict",
        description="동일한 세션 식별자로 저장한 요청 내용과 다릅니다.",
    ),
    ErrorCode.ACTIVITY_DEVICE_NOT_FOUND: ErrorDocs(
        summary="Activity device not found",
        description=("Device를 찾을 수 없거나 인증된 계정에 속하지 않습니다."),
    ),
    ErrorCode.ACTIVITY_EVENT_ID_CONFLICT: ErrorDocs(
        summary="Activity event ID conflict",
        description="같은 eventId로 저장된 활동 레코드와 요청 내용이 다릅니다.",
    ),
    ErrorCode.ACTIVITY_SEQUENCE_CONFLICT: ErrorDocs(
        summary="Activity sequence conflict",
        description=(
            "같은 Device와 sequence로 저장된 이벤트가 요청 eventId와 다릅니다."
        ),
    ),
    ErrorCode.ACTIVITY_BATCH_CONFLICT: ErrorDocs(
        summary="Activity batch conflict",
        description="같은 계정·Device·batchId로 처리한 배치의 내용과 다릅니다.",
    ),
    ErrorCode.ACTIVITY_BATCH_TOO_LARGE: ErrorDocs(
        summary="Activity batch too large",
        description="비압축 UTF-8 JSON 본문은 512 KiB 이하입니다.",
    ),
    ErrorCode.ACTIVITY_DELIVERY_UNAVAILABLE: ErrorDocs(
        summary="Activity delivery unavailable",
        description="활동 배치를 수락할 수 없습니다. 같은 batchId와 내용으로 재시도합니다.",
    ),
    ErrorCode.ACTIVITY_TIMELINE_BUSY: ErrorDocs(
        summary="Activity timeline busy",
        description="계정별 관찰 쓰기 잠금을 3초 안에 얻지 못했습니다.",
    ),
    ErrorCode.ACTIVITY_SEGMENT_NOT_FOUND: ErrorDocs(
        summary="Activity segment not found",
        description="관찰 구간을 찾을 수 없거나 인증된 계정에 속하지 않습니다.",
    ),
    ErrorCode.ACTIVITY_SEGMENT_NOT_LABELABLE: ErrorDocs(
        summary="Activity segment is not labelable",
        description="열린 구간·불투명 활동·수집 공백은 라벨 확정 대상이 아닙니다.",
    ),
    ErrorCode.ACTIVITY_SEGMENT_CHANGED: ErrorDocs(
        summary="Activity segment changed",
        description="관찰 구간이 재구성되었으므로 최신 상태를 다시 조회해야 합니다.",
    ),
    ErrorCode.ACTIVITY_LABEL_CONFIRMATION_CONFLICT: ErrorDocs(
        summary="Activity label confirmation conflict",
        description=(
            "같은 관찰 버전에 다른 선택이 이미 확정되어 있으므로 덮어쓸 수 없습니다."
        ),
        example_details=(
            ValidationDetail(
                loc=["body", "items", 0, "selection"],
                msg="Activity label confirmation conflicts with an existing selection",
                type="activity_label_confirmation_conflict",
            ),
        ),
    ),
    ErrorCode.LABEL_NOT_AVAILABLE: ErrorDocs(
        summary="Label not available",
        description="라벨을 찾을 수 없거나 현재 선택할 수 없는 상태입니다.",
    ),
}

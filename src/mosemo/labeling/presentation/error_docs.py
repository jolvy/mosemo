from collections.abc import Mapping

from mosemo.labeling.presentation.errors import LabelingErrorCode
from mosemo.shared.errors import ApiErrorCode
from mosemo.shared.openapi import ErrorDocs
from mosemo.shared.presentation.schemas import ValidationDetail

ERROR_DOCS: Mapping[ApiErrorCode, ErrorDocs] = {
    LabelingErrorCode.LABELING_SEGMENT_NOT_FOUND: ErrorDocs(
        summary="Activity segment not found",
        description="관찰 구간을 찾을 수 없거나 인증된 계정에 속하지 않습니다.",
    ),
    LabelingErrorCode.LABELING_SEGMENT_NOT_LABELABLE: ErrorDocs(
        summary="Activity segment is not labelable",
        description="열린 구간·불투명 활동·수집 공백은 라벨 확정 대상이 아닙니다.",
    ),
    LabelingErrorCode.LABELING_SEGMENT_CHANGED: ErrorDocs(
        summary="Activity segment changed",
        description="관찰 구간이 재구성되었으므로 최신 상태를 다시 조회해야 합니다.",
    ),
    LabelingErrorCode.LABELING_CONFIRMATION_CONFLICT: ErrorDocs(
        summary="Activity label confirmation conflict",
        description=(
            "같은 관찰 버전에 다른 선택이 이미 확정되어 있으므로 덮어쓸 수 없습니다."
        ),
        example_details=(
            ValidationDetail(
                loc=["body", "items", 0, "selection"],
                msg="Activity label confirmation conflicts with an existing selection",
                type="labeling_confirmation_conflict",
            ),
        ),
    ),
}

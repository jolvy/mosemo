from collections.abc import Mapping

from mosemo.shared.errors import ApiErrorCode, CommonErrorCode
from mosemo.shared.openapi import ErrorDocs
from mosemo.shared.presentation.schemas import ValidationDetail

ERROR_DOCS: Mapping[ApiErrorCode, ErrorDocs] = {
    CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN: ErrorDocs(
        summary="Invalid access token",
        description="Bearer 액세스 토큰이 유효하지 않거나 누락되었습니다.",
    ),
    CommonErrorCode.REQUEST_ROUTE_NOT_FOUND: ErrorDocs(
        summary="Route not found", description="요청한 API route를 찾을 수 없습니다."
    ),
    CommonErrorCode.REQUEST_METHOD_NOT_ALLOWED: ErrorDocs(
        summary="Method not allowed",
        description="요청 method가 해당 API route에서 허용되지 않습니다.",
    ),
    CommonErrorCode.INVALID_ARGUMENT: ErrorDocs(
        summary="Invalid argument",
        description="요청 입력값 검증에 실패했습니다.",
        example_details=(
            ValidationDetail(
                loc=["body", "users", 0, "email"],
                msg="Field required",
                type="missing",
            ),
        ),
    ),
    CommonErrorCode.INTERNAL_SERVER_ERROR: ErrorDocs(
        summary="Internal server error",
        description="예상하지 못한 서버 오류가 발생했습니다.",
    ),
}

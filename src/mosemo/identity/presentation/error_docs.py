from collections.abc import Mapping

from mosemo.identity.presentation.errors import IdentityErrorCode
from mosemo.shared.errors import ApiErrorCode
from mosemo.shared.openapi import ErrorDocs

ERROR_DOCS: Mapping[ApiErrorCode, ErrorDocs] = {
    IdentityErrorCode.AUTH_INVALID_AUTHORIZATION_CODE: ErrorDocs(
        summary="Invalid authorization code",
        description="authorization code 또는 PKCE 검증에 실패했습니다.",
    ),
    IdentityErrorCode.AUTH_INVALID_OAUTH_CONTEXT: ErrorDocs(
        summary="Invalid OAuth context",
        description="OAuth login context가 유효하지 않습니다.",
    ),
}

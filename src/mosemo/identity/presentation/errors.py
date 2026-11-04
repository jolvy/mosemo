from mosemo.shared.errors import ApiErrorCode, ErrorSpec


class IdentityErrorCode(ApiErrorCode):
    AUTH_INVALID_AUTHORIZATION_CODE = ErrorSpec(
        code=400,
        message="Invalid or expired authorization code",
    )
    AUTH_INVALID_OAUTH_CONTEXT = ErrorSpec(
        code=400,
        message="Invalid or expired OAuth login context",
    )

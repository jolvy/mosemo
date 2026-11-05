import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Query, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from mosemo.identity.application.ports import OAuthClient
from mosemo.identity.application.sign_in import (
    ExchangeAuthorizationCode,
    InvalidAuthorizationCodeError,
    LogIn,
    OAuthAuthenticationError,
    OAuthClientResolver,
)
from mosemo.identity.application.tokens import (
    InvalidAccessTokenError,
    TokenService,
)
from mosemo.identity.domain.accounts import AccountProvider
from mosemo.identity.domain.pkce import (
    PKCE_CODE_CHALLENGE_PATTERN,
    is_valid_code_challenge,
)
from mosemo.identity.presentation.auth.schemas import (
    KakaoCallback,
    TokenRequest,
    TokenResponse,
)
from mosemo.identity.presentation.dependencies import SessionDep
from mosemo.identity.presentation.errors import IdentityErrorCode
from mosemo.shared.errors import ApiException, CommonErrorCode
from mosemo.shared.openapi import api_error_responses
from mosemo.shared.presentation.current_account import (
    BearerCredentialsDep,
    CurrentAccount,
)
from mosemo.shared.presentation.responses import error_response

logger = logging.getLogger(__name__)


STATE_COOKIE = "kakao_oauth_state"
PKCE_CHALLENGE_COOKIE = "kakao_oauth_pkce_challenge"
OAUTH_COOKIE_PATH = "/api/v1/auth/kakao"
OAUTH_COOKIE_MAX_AGE_SECONDS = 600


def _prevent_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _set_oauth_cookie(
    response: RedirectResponse,
    *,
    key: str,
    value: str,
    secure: bool,
) -> None:
    response.set_cookie(
        key=key,
        value=value,
        max_age=OAUTH_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path=OAUTH_COOKIE_PATH,
    )


def _clear_oauth_cookies(
    response: Response,
    *,
    secure: bool,
) -> None:
    for key in (STATE_COOKIE, PKCE_CHALLENGE_COOKIE):
        response.delete_cookie(
            key=key,
            httponly=True,
            secure=secure,
            samesite="lax",
            path=OAUTH_COOKIE_PATH,
        )


def _app_redirect(callback_uri: str, **query: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{callback_uri}?{urlencode(query)}",
        status_code=status.HTTP_302_FOUND,
    )


def _invalid_state_response() -> JSONResponse:
    return error_response(IdentityErrorCode.AUTH_INVALID_OAUTH_CONTEXT)


def _finalize_oauth_callback_response(
    response: Response,
    *,
    secure: bool,
) -> None:
    _prevent_caching(response)
    _clear_oauth_cookies(response, secure=secure)


LOGIN_RESPONSES = {
    status.HTTP_302_FOUND: {
        "headers": {
            "Location": {
                "description": "Kakao 인증 화면의 URL입니다.",
                "schema": {"type": "string", "format": "uri"},
            },
            "Set-Cookie": {
                "description": (
                    "10분 동안 유지되는 HttpOnly state 및 PKCE challenge "
                    "쿠키입니다. 운영 환경에서는 Secure 속성도 사용합니다."
                ),
                "schema": {"type": "string"},
            },
            "Cache-Control": {
                "description": "응답을 저장하지 않도록 no-store로 설정됩니다.",
                "schema": {"type": "string", "const": "no-store"},
            },
            "Pragma": {
                "description": "이전 HTTP 캐시와의 호환을 위해 no-cache로 설정됩니다.",
                "schema": {"type": "string", "const": "no-cache"},
            },
        }
    },
    **api_error_responses(
        CommonErrorCode.INVALID_ARGUMENT,
    ),
}

CALLBACK_RESPONSES = {
    status.HTTP_302_FOUND: {
        "headers": {
            "Location": {
                "description": (
                    "성공 시 code, 실패 시 access_denied 또는 "
                    "authentication_failed를 포함하는 macOS custom scheme URI입니다."
                ),
                "schema": {"type": "string", "format": "uri"},
            },
            "Set-Cookie": {
                "description": "state 및 PKCE challenge 쿠키를 즉시 만료시킵니다.",
                "schema": {"type": "string"},
            },
            "Cache-Control": {
                "description": "응답을 저장하지 않도록 no-store로 설정됩니다.",
                "schema": {"type": "string", "const": "no-store"},
            },
            "Pragma": {
                "description": "이전 HTTP 캐시와의 호환을 위해 no-cache로 설정됩니다.",
                "schema": {"type": "string", "const": "no-cache"},
            },
        }
    },
    **api_error_responses(
        IdentityErrorCode.AUTH_INVALID_OAUTH_CONTEXT,
        headers={
            status.HTTP_400_BAD_REQUEST: {
                "Set-Cookie": {
                    "description": "state 및 PKCE challenge 쿠키를 즉시 만료시킵니다.",
                    "schema": {"type": "string"},
                },
                "Cache-Control": {
                    "description": "응답을 저장하지 않도록 no-store로 설정됩니다.",
                    "schema": {"type": "string", "const": "no-store"},
                },
                "Pragma": {
                    "description": (
                        "이전 HTTP 캐시와의 호환을 위해 no-cache로 설정됩니다."
                    ),
                    "schema": {"type": "string", "const": "no-cache"},
                },
            }
        },
    ),
}

TOKEN_RESPONSES = {
    status.HTTP_200_OK: {
        "headers": {
            "Cache-Control": {
                "description": "응답을 저장하지 않도록 no-store로 설정됩니다.",
                "schema": {"type": "string", "const": "no-store"},
            },
            "Pragma": {
                "description": "이전 HTTP 캐시와의 호환을 위해 no-cache로 설정됩니다.",
                "schema": {"type": "string", "const": "no-cache"},
            },
        }
    },
    **api_error_responses(
        IdentityErrorCode.AUTH_INVALID_AUTHORIZATION_CODE,
        CommonErrorCode.INVALID_ARGUMENT,
    ),
}


class OAuthCookies:
    """The OAuth state and PKCE challenge cookies that the login step set."""

    def __init__(
        self,
        state_cookie: Annotated[
            str | None,
            Cookie(
                alias=STATE_COOKIE,
                description="로그인 시작 시 서버가 저장한 OAuth state 쿠키입니다.",
            ),
        ] = None,
        pkce_challenge_cookie: Annotated[
            str | None,
            Cookie(
                alias=PKCE_CHALLENGE_COOKIE,
                description="로그인 시작 시 서버가 저장한 PKCE code challenge 쿠키입니다.",
            ),
        ] = None,
    ) -> None:
        self.state = state_cookie
        self.pkce_challenge = pkce_challenge_cookie


def _checked_callback(
    kakao_callback: KakaoCallback, cookies: OAuthCookies, *, callback_uri: str
) -> Response | tuple[str, str]:
    """Return the provider code and PKCE challenge to sign in with, or the
    response for a callback that must not sign in."""
    state_cookie = cookies.state
    pkce_challenge_cookie = cookies.pkce_challenge
    if (
        kakao_callback.state is None
        or state_cookie is None
        or pkce_challenge_cookie is None
        or not is_valid_code_challenge(pkce_challenge_cookie)
        or not secrets.compare_digest(kakao_callback.state, state_cookie)
    ):
        return _invalid_state_response()
    if kakao_callback.error is not None:
        public_error = (
            "access_denied"
            if kakao_callback.error == "access_denied"
            else "authentication_failed"
        )
        if public_error == "access_denied":
            logger.info("Kakao authorization cancelled")
        else:
            logger.warning("Kakao authorization failed")
        return _app_redirect(callback_uri, error=public_error)
    if kakao_callback.code is None:
        return _app_redirect(callback_uri, error="authentication_failed")
    return kakao_callback.code, pkce_challenge_cookie


def create_current_account_provider(
    token_service: TokenService,
) -> Callable[..., Awaitable[CurrentAccount]]:
    async def get_token_current_account(
        credentials: BearerCredentialsDep,
    ) -> CurrentAccount:
        """Current account provider: verifies only the access token (ADR 0011).

        It checks the signature and expiry and does not query the database.
        """
        if credentials is None:
            raise ApiException(CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN)

        try:
            account_id = token_service.decode_access_token(credentials.credentials)
        except InvalidAccessTokenError as exc:
            raise ApiException(CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc

        return CurrentAccount(account_id=account_id)

    return get_token_current_account


def create_oauth_router(
    *,
    log_in: LogIn,
    get_login_client: Callable[..., OAuthClient],
    get_oauth_clients: Callable[..., OAuthClientResolver],
    macos_callback_uri: str,
    secure_cookies: bool,
) -> APIRouter:
    """Build the Kakao login and callback routes.

    The providers resolve OAuth clients per request.
    """
    router = APIRouter()

    @router.get(
        "/kakao/login",
        operation_id="authKakaoLogin",
        status_code=status.HTTP_302_FOUND,
        response_class=RedirectResponse,
        summary="Kakao 로그인 시작",
        description=(
            "macOS 앱이 생성한 PKCE code challenge를 저장하고 "
            "Kakao 인증 화면으로 이동시킵니다."
        ),
        response_description="Kakao 인증 화면으로 이동하는 리다이렉트 응답입니다.",
        responses=LOGIN_RESPONSES,
    )
    def login(
        kakao_client: Annotated[OAuthClient, Depends(get_login_client)],
        *,
        code_challenge: Annotated[
            str,
            Query(
                pattern=PKCE_CODE_CHALLENGE_PATTERN,
                description=(
                    "macOS 앱이 code_verifier로부터 S256 방식으로 생성한 "
                    "PKCE code challenge입니다."
                ),
            ),
        ],
        code_challenge_method: Annotated[
            Literal["S256"],
            Query(description="PKCE code challenge 생성 방식이며 S256으로 고정됩니다."),
        ],
    ) -> RedirectResponse:
        state = secrets.token_urlsafe(32)
        response = RedirectResponse(
            url=kakao_client.create_authorization_url(state=state),
            status_code=status.HTTP_302_FOUND,
        )
        _prevent_caching(response)
        _set_oauth_cookie(
            response, key=STATE_COOKIE, value=state, secure=secure_cookies
        )
        _set_oauth_cookie(
            response,
            key=PKCE_CHALLENGE_COOKIE,
            value=code_challenge,
            secure=secure_cookies,
        )
        return response

    @router.get(
        "/kakao/callback",
        operation_id="authKakaoCallback",
        status_code=status.HTTP_302_FOUND,
        response_class=RedirectResponse,
        summary="Kakao 로그인 callback 처리",
        description=(
            "Kakao 인증 결과와 로그인 요청의 state 및 PKCE 쿠키를 검증합니다. "
            "성공하면 일회용 인증 코드를, 실패하면 공개 오류 코드를 macOS 앱에 전달합니다."
        ),
        response_description="인증 결과를 macOS 앱에 전달하는 리다이렉트 응답입니다.",
        responses=CALLBACK_RESPONSES,
    )
    async def callback(
        db_session: SessionDep,
        *,
        oauth_clients: Annotated[OAuthClientResolver, Depends(get_oauth_clients)],
        kakao_callback: Annotated[KakaoCallback, Query()],
        cookies: Annotated[OAuthCookies, Depends()],
    ):
        checked = _checked_callback(
            kakao_callback, cookies, callback_uri=macos_callback_uri
        )
        if isinstance(checked, Response):
            response = checked
        else:
            code, code_challenge = checked
            try:
                authorization_code = await log_in.execute(
                    db_session,
                    oauth_clients=oauth_clients,
                    provider=AccountProvider.KAKAO,
                    code=code,
                    code_challenge=code_challenge,
                )
            except OAuthAuthenticationError:
                logger.exception("Kakao authentication failed")
                response = _app_redirect(
                    macos_callback_uri, error="authentication_failed"
                )
            except Exception:
                logger.exception("Native authorization code issuance failed")
                response = _app_redirect(
                    macos_callback_uri, error="authentication_failed"
                )
            else:
                response = _app_redirect(macos_callback_uri, code=authorization_code)

        _finalize_oauth_callback_response(response, secure=secure_cookies)
        return response

    return router


def create_token_router(
    *,
    exchange_authorization_code: ExchangeAuthorizationCode,
    token_service: TokenService,
    access_token_ttl_seconds: int,
) -> APIRouter:
    """Build the route that exchanges a one-time code for an access token."""
    router = APIRouter()

    @router.post(
        "/token",
        operation_id="authExchangeToken",
        response_model=TokenResponse,
        summary="액세스 토큰 발급",
        description=(
            "Kakao 로그인 callback에서 발급한 일회용 인증 코드와 "
            "PKCE code verifier를 검증한 뒤 Mosemo 액세스 토큰을 발급합니다."
        ),
        response_description="Mosemo Bearer 액세스 토큰입니다.",
        responses=TOKEN_RESPONSES,
    )
    async def exchange_token(
        request: TokenRequest,
        db_session: SessionDep,
        *,
        response: Response,
    ) -> TokenResponse:
        try:
            access_token = await exchange_authorization_code.execute(
                db_session,
                authorization_code=request.code,
                code_verifier=request.code_verifier,
                token_service=token_service,
            )
        except InvalidAuthorizationCodeError as exc:
            raise ApiException(
                IdentityErrorCode.AUTH_INVALID_AUTHORIZATION_CODE
            ) from exc

        _prevent_caching(response)
        return TokenResponse(
            access_token=access_token,
            expires_in=access_token_ttl_seconds,
        )

    return router

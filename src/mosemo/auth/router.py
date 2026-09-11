import logging
import secrets
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Query, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from mosemo.auth.pkce import (
    PKCE_CODE_CHALLENGE_PATTERN,
    is_valid_code_challenge,
)
from mosemo.auth.schemas import TokenRequest, TokenResponse
from mosemo.auth.service import (
    InvalidAuthorizationCodeError,
    KakaoAuthenticationError,
)
from mosemo.dependencies import AuthServiceDep, ConfigDep
from mosemo.exceptions import (
    ApiException,
    ErrorCode,
)
from mosemo.openapi import api_error_responses
from mosemo.responses import error_response

logger = logging.getLogger(__name__)

router = APIRouter()

KAKAO_AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
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
    config: ConfigDep,
) -> None:
    response.set_cookie(
        key=key,
        value=value,
        max_age=OAUTH_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=config.app_env == "prod",
        samesite="lax",
        path=OAUTH_COOKIE_PATH,
    )


def _clear_oauth_cookies(
    response: Response,
    *,
    config: ConfigDep,
) -> None:
    for key in (STATE_COOKIE, PKCE_CHALLENGE_COOKIE):
        response.delete_cookie(
            key=key,
            httponly=True,
            secure=config.app_env == "prod",
            samesite="lax",
            path=OAUTH_COOKIE_PATH,
        )


def _app_redirect(config: ConfigDep, **query: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{config.auth.macos_callback_uri}?{urlencode(query)}",
        status_code=status.HTTP_302_FOUND,
    )


def _invalid_state_response() -> JSONResponse:
    return error_response(ErrorCode.AUTH_INVALID_OAUTH_CONTEXT)


def _finalize_oauth_callback_response(
    response: Response,
    *,
    config: ConfigDep,
) -> None:
    _prevent_caching(response)
    _clear_oauth_cookies(response, config=config)


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
    responses={
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
            ErrorCode.INVALID_ARGUMENT,
        ),
    },
)
def login(
    config: ConfigDep,
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
    query = urlencode(
        {
            "client_id": config.kakao.rest_api_key,
            "redirect_uri": config.kakao.redirect_uri,
            "response_type": "code",
            "state": state,
        }
    )

    response = RedirectResponse(
        url=f"{KAKAO_AUTHORIZE_URL}?{query}",
        status_code=status.HTTP_302_FOUND,
    )
    _prevent_caching(response)
    _set_oauth_cookie(response, key=STATE_COOKIE, value=state, config=config)
    _set_oauth_cookie(
        response,
        key=PKCE_CHALLENGE_COOKIE,
        value=code_challenge,
        config=config,
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
    responses={
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
            ErrorCode.AUTH_INVALID_OAUTH_CONTEXT,
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
    },
)
async def callback(
    service: AuthServiceDep,
    config: ConfigDep,
    code: Annotated[
        str | None,
        Query(description="Kakao가 인증 성공 시 전달한 authorization code입니다."),
    ] = None,
    state: Annotated[
        str | None,
        Query(
            description=(
                "로그인 요청과 callback을 연결하고 CSRF를 방지하는 state 값입니다."
            )
        ),
    ] = None,
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
    error: Annotated[
        str | None,
        Query(description="Kakao가 인증 실패 또는 취소 시 전달한 오류 코드입니다."),
    ] = None,
    error_description: Annotated[
        str | None,
        Query(
            description=(
                "Kakao가 전달한 상세 오류 설명입니다. "
                "서버는 민감 정보 노출을 방지하기 위해 사용하지 않습니다."
            )
        ),
    ] = None,
):
    if (
        state is None
        or state_cookie is None
        or pkce_challenge_cookie is None
        or not is_valid_code_challenge(pkce_challenge_cookie)
        or not secrets.compare_digest(state, state_cookie)
    ):
        response = _invalid_state_response()
    elif error is not None:
        public_error = (
            "access_denied" if error == "access_denied" else "authentication_failed"
        )
        if public_error == "access_denied":
            logger.info("Kakao authorization cancelled")
        else:
            logger.warning("Kakao authorization failed")
        response = _app_redirect(config, error=public_error)
    elif code is None:
        response = _app_redirect(config, error="authentication_failed")
    else:
        try:
            account = await service.authenticate_kakao(code=code)
            authorization_code = await service.create_authorization_code(
                account=account,
                code_challenge=pkce_challenge_cookie,
            )
        except KakaoAuthenticationError:
            logger.exception("Kakao authentication failed")
            response = _app_redirect(config, error="authentication_failed")
        except Exception:
            logger.exception("Native authorization code issuance failed")
            response = _app_redirect(config, error="authentication_failed")
        else:
            response = _app_redirect(config, code=authorization_code)

    _finalize_oauth_callback_response(response, config=config)
    return response


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
    responses={
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
            ErrorCode.AUTH_INVALID_AUTHORIZATION_CODE,
            ErrorCode.INVALID_ARGUMENT,
        ),
    },
)
async def exchange_token(
    request: TokenRequest,
    service: AuthServiceDep,
    config: ConfigDep,
    response: Response,
) -> TokenResponse:
    try:
        access_token = await service.exchange_authorization_code(
            authorization_code=request.code,
            code_verifier=request.code_verifier,
        )
    except InvalidAuthorizationCodeError as exc:
        raise ApiException(ErrorCode.AUTH_INVALID_AUTHORIZATION_CODE) from exc

    _prevent_caching(response)
    return TokenResponse(
        access_token=access_token,
        expires_in=config.auth.access_token_ttl_seconds,
    )

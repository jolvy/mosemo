import logging
import secrets
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, HTTPException, Query, Response, status
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
    response: JSONResponse | RedirectResponse,
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
    response = RedirectResponse(
        url=f"{config.auth.macos_callback_uri}?{urlencode(query)}",
        status_code=status.HTTP_302_FOUND,
    )
    _prevent_caching(response)
    return response


def _invalid_state_response(config: ConfigDep) -> JSONResponse:
    response = JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid Kakao OAuth state"},
    )
    _prevent_caching(response)
    _clear_oauth_cookies(response, config=config)
    return response


@router.get("/kakao/login")
def login(
    config: ConfigDep,
    code_challenge: Annotated[
        str,
        Query(pattern=PKCE_CODE_CHALLENGE_PATTERN),
    ],
    code_challenge_method: Annotated[Literal["S256"], Query()],
):
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


@router.get("/kakao/callback")
async def callback(
    service: AuthServiceDep,
    config: ConfigDep,
    code: str | None = None,
    state: str | None = None,
    state_cookie: str | None = Cookie(default=None, alias=STATE_COOKIE),
    pkce_challenge_cookie: str | None = Cookie(
        default=None,
        alias=PKCE_CHALLENGE_COOKIE,
    ),
    error: str | None = None,
    error_description: str | None = None,
):
    if (
        state is None
        or state_cookie is None
        or pkce_challenge_cookie is None
        or not is_valid_code_challenge(pkce_challenge_cookie)
        or not secrets.compare_digest(state, state_cookie)
    ):
        return _invalid_state_response(config)

    if error is not None:
        public_error = (
            "access_denied" if error == "access_denied" else "authentication_failed"
        )
        if public_error == "access_denied":
            logger.info("Kakao authorization cancelled")
        else:
            logger.warning("Kakao authorization failed")
        response = _app_redirect(config, error=public_error)
        _clear_oauth_cookies(response, config=config)
        return response

    if code is None:
        response = _app_redirect(config, error="authentication_failed")
        _clear_oauth_cookies(response, config=config)
        return response

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

    _clear_oauth_cookies(response, config=config)
    return response


@router.post("/token", response_model=TokenResponse)
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired authorization code",
        ) from exc

    _prevent_caching(response)
    return TokenResponse(
        access_token=access_token,
        expires_in=config.auth.access_token_ttl_seconds,
    )

import logging
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, HTTPException, status
from fastapi.responses import RedirectResponse

from mosemo.auth.service import KakaoAuthenticationError
from mosemo.dependencies import AuthServiceDep, ConfigDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kakao")

KAKAO_AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
STATE_COOKIE = "kakao_oauth_state"


@router.get("/login")
def login(config: ConfigDep):
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
    response.set_cookie(
        key=STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        secure=config.app_env == "prod",  # 로컬 HTTP 전용. 운영 HTTPS에서는 True
        samesite="lax",
        path="/api/v1/auth/kakao",
    )
    return response


@router.get("/callback")
async def callback(
    service: AuthServiceDep,
    code: str | None = None,
    state: str | None = None,
    state_cookie: str | None = Cookie(default=None, alias=STATE_COOKIE),
    error: str | None = None,
    error_description: str | None = None,
):
    if error is not None:
        # logger.error()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"kakao login failed: {error}",
        )

    if (
        code is None
        or state is None
        or state_cookie is None
        or not secrets.compare_digest(state, state_cookie)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Kakao OAuth state",
        )

    try:
        return await service.authenticate_kakao(code=code)
    except KakaoAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Kakao authentication failed",
        ) from exc

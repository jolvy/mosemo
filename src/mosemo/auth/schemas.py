from typing import Literal

from pydantic import BaseModel, Field

from mosemo.schemas import ApiRequestModel, ApiResponseModel


class KakaoCallback(BaseModel):
    code: str | None = Field(
        default=None,
        description="Kakao가 인증 성공 시 전달한 authorization code입니다.",
    )
    state: str | None = Field(
        default=None,
        description="로그인 요청과 callback을 연결하고 CSRF를 방지하는 state 값입니다.",
    )
    error: str | None = Field(
        default=None,
        description="Kakao가 인증 실패 또는 취소 시 전달한 오류 코드입니다.",
    )
    error_description: str | None = Field(
        default=None,
        description=(
            "Kakao가 전달한 상세 오류 설명입니다. "
            "서버는 민감 정보 노출을 방지하기 위해 사용하지 않습니다."
        ),
    )


class TokenRequest(ApiRequestModel):
    """일회용 인증 코드를 Mosemo 액세스 토큰으로 교환하는 요청입니다."""

    grant_type: Literal["authorization_code"] = Field(
        description="토큰 발급 방식이며 authorization_code만 허용합니다."
    )
    code: str = Field(
        description="Kakao 로그인 callback에서 macOS 앱으로 전달한 일회용 인증 코드입니다."
    )
    code_verifier: str = Field(
        description=(
            "로그인 요청의 code_challenge를 생성할 때 사용한 "
            "43~128자의 PKCE code verifier입니다."
        )
    )


class TokenResponse(ApiResponseModel):
    """Mosemo API 인증에 사용하는 액세스 토큰 응답입니다."""

    access_token: str = Field(
        description="Authorization 헤더에 사용할 Mosemo 액세스 토큰입니다."
    )
    token_type: Literal["Bearer"] = Field(
        default="Bearer",
        description="액세스 토큰의 인증 방식이며 Bearer로 고정됩니다.",
    )
    expires_in: int = Field(
        description="액세스 토큰이 발급 시점부터 유효한 시간(초)입니다."
    )

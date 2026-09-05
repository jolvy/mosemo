from fastapi import APIRouter, status

from mosemo.accounts.schemas import AccountResponse
from mosemo.dependencies import CurrentAccountDep
from mosemo.schemas import ApiErrorResponse

router = APIRouter(prefix="/accounts")


@router.get(
    "/me",
    operation_id="accountsGetMe",
    response_model=AccountResponse,
    summary="현재 계정 조회",
    description="Bearer 액세스 토큰으로 인증된 Mosemo 계정 정보를 조회합니다.",
    response_description="현재 인증된 계정의 공개 정보입니다.",
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ApiErrorResponse,
            "description": (
                "액세스 토큰이 누락되었거나 유효하지 않거나 만료되었거나, "
                "토큰의 계정이 존재하지 않습니다."
            ),
            "headers": {
                "WWW-Authenticate": {
                    "description": "클라이언트가 사용해야 하는 인증 방식입니다.",
                    "schema": {"type": "string", "const": "Bearer"},
                }
            },
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Invalid or expired access token",
                    }
                }
            },
        }
    },
)
def get_me(account: CurrentAccountDep) -> AccountResponse:
    return AccountResponse.model_validate(account)

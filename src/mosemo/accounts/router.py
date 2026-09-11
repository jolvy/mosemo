from fastapi import APIRouter

from mosemo.accounts.schemas import AccountResponse
from mosemo.dependencies import CurrentAccountDep
from mosemo.exceptions import ErrorCode
from mosemo.openapi import api_error_responses

router = APIRouter(prefix="/accounts")


@router.get(
    "/me",
    operation_id="accountsGetMe",
    response_model=AccountResponse,
    summary="현재 계정 조회",
    description="Bearer 액세스 토큰으로 인증된 Mosemo 계정 정보를 조회합니다.",
    response_description="현재 인증된 계정의 공개 정보입니다.",
    responses=api_error_responses(
        ErrorCode.AUTH_INVALID_ACCESS_TOKEN,
    ),
)
def get_me(account: CurrentAccountDep) -> AccountResponse:
    return AccountResponse.model_validate(account)

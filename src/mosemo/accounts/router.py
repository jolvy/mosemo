from fastapi import APIRouter

from mosemo.accounts.schemas import AccountResponse
from mosemo.accounts.service import AccountNotFoundError
from mosemo.dependencies import AccountServiceDep, AuthenticatedAccountDep
from mosemo.exceptions import ApiException, ErrorCode
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
async def get_me(
    authenticated_account: AuthenticatedAccountDep,
    service: AccountServiceDep,
) -> AccountResponse:
    try:
        account = await service.get_account(authenticated_account.account_id)
    except AccountNotFoundError as exc:
        raise ApiException(ErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc
    return AccountResponse.model_validate(account)

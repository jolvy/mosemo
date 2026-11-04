from fastapi import APIRouter

from mosemo.identity.application.accounts import AccountNotFoundError, GetAccount
from mosemo.identity.presentation.accounts.schemas import AccountResponse
from mosemo.identity.presentation.dependencies import SessionDep
from mosemo.shared.errors import ApiException, CommonErrorCode
from mosemo.shared.openapi import api_error_responses
from mosemo.shared.presentation.current_account import CurrentAccountDep


def create_router(*, get_account: GetAccount) -> APIRouter:
    router = APIRouter(prefix="/accounts")

    @router.get(
        "/me",
        operation_id="accountsGetMe",
        response_model=AccountResponse,
        summary="현재 계정 조회",
        description="Bearer 액세스 토큰으로 인증된 Mosemo 계정 정보를 조회합니다.",
        response_description="현재 인증된 계정의 공개 정보입니다.",
        responses=api_error_responses(
            CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN,
        ),
    )
    async def get_me(
        current_account: CurrentAccountDep,
        db_session: SessionDep,
    ) -> AccountResponse:
        try:
            account = await get_account.execute(db_session, current_account.account_id)
        except AccountNotFoundError as exc:
            raise ApiException(CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc
        return AccountResponse.from_account(account)

    return router

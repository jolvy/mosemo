from fastapi import APIRouter

from mosemo.accounts.service import AccountNotFoundError
from mosemo.activity_labels.schemas import LabelResponse
from mosemo.dependencies import (
    AccountServiceDep,
    AuthenticatedAccountDep,
    LabelCatalogRepositoryDep,
)
from mosemo.exceptions import ApiException, ErrorCode
from mosemo.openapi import api_error_responses

router = APIRouter(prefix="/labels")


@router.get(
    "",
    operation_id="labelsList",
    response_model=list[LabelResponse],
    summary="내 라벨 목록 조회",
    description="인증된 계정의 사용 중인 라벨과 보관한 라벨을 함께 반환합니다.",
    response_description="계정의 전체 라벨 배열입니다.",
    responses=api_error_responses(ErrorCode.AUTH_INVALID_ACCESS_TOKEN),
)
async def list_labels(
    authenticated_account: AuthenticatedAccountDep,
    account_service: AccountServiceDep,
    repository: LabelCatalogRepositoryDep,
) -> list[LabelResponse]:
    account_id = authenticated_account.account_id
    try:
        await account_service.get_account(account_id)
    except AccountNotFoundError as exc:
        raise ApiException(ErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc

    labels = await repository.list_owned(account_id=account_id)
    return [LabelResponse.model_validate(label) for label in labels]

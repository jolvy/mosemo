from fastapi import APIRouter

from mosemo.activity_labels.schemas import LabelResponse
from mosemo.api.dependencies import (
    AccountDirectoryDep,
    AuthenticatedAccountDep,
    LabelCatalogRepositoryDep,
    SessionDep,
)
from mosemo.identity.public import AccountNotFoundError
from mosemo.shared.errors import ApiException, CommonErrorCode
from mosemo.shared.openapi import api_error_responses

router = APIRouter(prefix="/labels")


@router.get(
    "",
    operation_id="labelsList",
    response_model=list[LabelResponse],
    summary="내 라벨 목록 조회",
    description="인증된 계정의 사용 중인 라벨과 보관한 라벨을 함께 반환합니다.",
    response_description="계정의 전체 라벨 배열입니다.",
    responses=api_error_responses(CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN),
)
async def list_labels(
    authenticated_account: AuthenticatedAccountDep,
    accounts: AccountDirectoryDep,
    *,
    session: SessionDep,
    repository: LabelCatalogRepositoryDep,
) -> list[LabelResponse]:
    account_id = authenticated_account.account_id
    try:
        await accounts.ensure_exists(session, account_id)
    except AccountNotFoundError as exc:
        raise ApiException(CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc

    labels = await repository.list_owned(account_id=account_id)
    return [LabelResponse.model_validate(label) for label in labels]

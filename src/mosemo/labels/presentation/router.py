from fastapi import APIRouter

from mosemo.identity.public import AccountNotFoundError
from mosemo.labels.application.labels import ListLabels
from mosemo.labels.presentation.dependencies import SessionDep
from mosemo.labels.presentation.schemas import LabelResponse
from mosemo.shared.errors import ApiException, CommonErrorCode
from mosemo.shared.openapi import api_error_responses
from mosemo.shared.presentation.current_account import CurrentAccountDep


def create_router(*, list_labels: ListLabels) -> APIRouter:
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
    async def list_labels_route(
        current_account: CurrentAccountDep,
        db_session: SessionDep,
    ) -> list[LabelResponse]:
        try:
            labels = await list_labels.execute(db_session, current_account.account_id)
        except AccountNotFoundError as exc:
            raise ApiException(CommonErrorCode.AUTH_INVALID_ACCESS_TOKEN) from exc
        return [LabelResponse.from_label(label) for label in labels]

    return router

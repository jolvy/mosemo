from fastapi import APIRouter

from mosemo.accounts.schemas import AccountResponse
from mosemo.dependencies import CurrentAccountDep

router = APIRouter(prefix="/accounts")


@router.get("/me", response_model=AccountResponse)
def get_me(account: CurrentAccountDep) -> AccountResponse:
    return AccountResponse.model_validate(account)

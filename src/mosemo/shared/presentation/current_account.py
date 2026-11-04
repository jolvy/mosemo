from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends


@dataclass(frozen=True, slots=True)
class CurrentAccount:
    account_id: UUID


async def get_current_account() -> CurrentAccount:
    """Declared provider; the assembly root overrides it with token verification.

    The override checks only the access token signature and expiry and does
    not query the database (ADR 0011).
    """
    raise NotImplementedError("the assembly root must override get_current_account")


CurrentAccountDep = Annotated[CurrentAccount, Depends(get_current_account)]

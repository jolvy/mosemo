from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


@dataclass(frozen=True, slots=True)
class CurrentAccount:
    account_id: UUID


# Declared with the provider so every route that takes the current account
# documents the same Bearer security scheme, whatever implementation overrides it.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Mosemo가 발급한 Bearer 액세스 토큰입니다.",
)
BearerCredentialsDep = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]


async def get_current_account(credentials: BearerCredentialsDep) -> CurrentAccount:
    """Declared provider; the assembly root overrides it with token verification.

    The override checks only the access token signature and expiry and does
    not query the database (ADR 0011).
    """
    raise NotImplementedError("the assembly root must override get_current_account")


CurrentAccountDep = Annotated[CurrentAccount, Depends(get_current_account)]

import asyncio
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from mosemo.auth.context import AuthenticatedAccount
from mosemo.auth.tokens import TokenService
from mosemo.config import Config
from mosemo.dependencies import get_authenticated_account


def test_authenticated_account_is_immutable() -> None:
    context = AuthenticatedAccount(account_id=uuid4())

    with pytest.raises(FrozenInstanceError):
        context.__setattr__("account_id", uuid4())


def test_get_authenticated_account_decodes_account_id(config: Config) -> None:
    account_id = uuid4()
    token_service = TokenService(config.auth)
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=token_service.issue_access_token(account_id),
    )

    context = asyncio.run(
        get_authenticated_account(
            credentials=credentials,
            token_service=token_service,
        )
    )

    assert context == AuthenticatedAccount(account_id=account_id)

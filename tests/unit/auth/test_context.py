import asyncio
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from mosemo.identity.presentation.auth.router import (
    get_token_current_account,
    get_token_service,
)
from mosemo.infra.clock import utc_now
from mosemo.infra.config import Config
from mosemo.shared.presentation.current_account import CurrentAccount


def test_authenticated_account_is_immutable() -> None:
    context = CurrentAccount(account_id=uuid4())

    with pytest.raises(FrozenInstanceError):
        context.__setattr__("account_id", uuid4())


def test_get_authenticated_account_decodes_account_id(config: Config) -> None:
    account_id = uuid4()
    token_service = get_token_service(config)
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=token_service.issue_access_token(account_id, now=utc_now()),
    )

    context = asyncio.run(
        get_token_current_account(
            credentials=credentials,
            token_service=token_service,
        )
    )

    assert context == CurrentAccount(account_id=account_id)

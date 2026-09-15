import asyncio
from unittest.mock import create_autospec
from uuid import uuid4

import pytest

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.accounts.service import AccountNotFoundError, AccountService


def test_get_account_returns_stored_account() -> None:
    account_id = uuid4()
    account = Account(
        account_id=account_id,
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    account_repository = create_autospec(AccountRepository, instance=True)
    account_repository.find_by_id.return_value = account
    service = AccountService(account_repository=account_repository)

    result = asyncio.run(service.get_account(account_id))

    assert result is account
    account_repository.find_by_id.assert_awaited_once_with(account_id)


def test_get_account_rejects_missing_account() -> None:
    account_id = uuid4()
    account_repository = create_autospec(AccountRepository, instance=True)
    account_repository.find_by_id.return_value = None
    service = AccountService(account_repository=account_repository)

    with pytest.raises(AccountNotFoundError):
        asyncio.run(service.get_account(account_id))

    account_repository.find_by_id.assert_awaited_once_with(account_id)

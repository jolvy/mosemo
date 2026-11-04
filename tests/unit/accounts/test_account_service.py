import asyncio
from unittest.mock import create_autospec, sentinel
from uuid import uuid4

import pytest

from mosemo.identity.application.accounts import AccountNotFoundError, GetAccount
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.infrastructure.accounts import AccountRepository
from tests.use_case_support import TransactionRecorder, record_transactions


@pytest.fixture
def snapshots(monkeypatch: pytest.MonkeyPatch) -> TransactionRecorder:
    return record_transactions(
        monkeypatch, "mosemo.identity.application.accounts", "read_snapshot"
    )


def test_get_account_returns_stored_account(snapshots: TransactionRecorder) -> None:
    account_id = uuid4()
    account = Account(
        id=account_id,
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    account_repository = create_autospec(AccountRepository, instance=True)
    account_repository.find.return_value = account
    service = GetAccount(accounts=account_repository)

    result = asyncio.run(service.execute(sentinel.db_session, account_id))

    assert result is account
    account_repository.find.assert_awaited_once_with(sentinel.db_session, account_id)
    assert snapshots.committed == 1


def test_get_account_rejects_missing_account(snapshots: TransactionRecorder) -> None:
    account_id = uuid4()
    account_repository = create_autospec(AccountRepository, instance=True)
    account_repository.find.return_value = None
    service = GetAccount(accounts=account_repository)

    with pytest.raises(AccountNotFoundError):
        asyncio.run(service.execute(sentinel.db_session, account_id))

    account_repository.find.assert_awaited_once_with(sentinel.db_session, account_id)

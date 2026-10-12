import asyncio
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.device_registration import DeviceRegistration
from mosemo.accounts.models import Account
from mosemo.accounts.repository import AccountRepository
from mosemo.accounts.service import AccountNotFoundError
from mosemo.devices.models import Device
from mosemo.devices.repository import DeviceRepository
from mosemo.devices.service import DeviceService


def make_service(account_id):
    session = create_autospec(AsyncSession, instance=True)
    repository = create_autospec(DeviceRepository, instance=True)
    accounts = create_autospec(AccountRepository, instance=True)
    accounts.find_by_id.return_value = Account(account_id=account_id)
    repository.find_by_idempotency_key.return_value = None
    service = DeviceService(
        session=session, repository=repository, account_repository=accounts
    )
    return service, repository, accounts


def test_create_device_commits_new_device(monkeypatch: pytest.MonkeyPatch) -> None:
    account_id = uuid4()
    service, repository, _ = make_service(account_id)
    idempotency_key = uuid4()
    candidate_id = uuid4()
    monkeypatch.setattr("mosemo.devices.service.uuid7", lambda: candidate_id)
    repository.insert.return_value = Device(
        device_id=candidate_id,
        account_id=account_id,
        idempotency_key=idempotency_key,
    )

    result = asyncio.run(
        service.create_device(account_id=account_id, idempotency_key=idempotency_key)
    )

    assert result.device_id == candidate_id


def test_create_device_returns_original_id_for_retry() -> None:
    account_id = uuid4()
    service, repository, _ = make_service(account_id)
    idempotency_key = uuid4()
    original_id = uuid4()
    repository.find_by_idempotency_key.return_value = DeviceRegistration(
        original_id, account_id, idempotency_key
    )

    result = asyncio.run(
        service.create_device(account_id=account_id, idempotency_key=idempotency_key)
    )

    assert result.device_id == original_id


def test_create_device_returns_committed_winner_after_insert_conflict() -> None:
    account_id = uuid4()
    service, repository, _ = make_service(account_id)
    idempotency_key = uuid4()
    winner_id = uuid4()
    repository.insert.return_value = None
    repository.find_by_idempotency_key.side_effect = [
        None,
        DeviceRegistration(winner_id, account_id, idempotency_key),
    ]

    result = asyncio.run(
        service.create_device(account_id=account_id, idempotency_key=idempotency_key)
    )

    assert result.device_id == winner_id


@pytest.mark.parametrize("mismatched_field", ["account_id", "idempotency_key"])
def test_create_device_rejects_mismatched_conflict_winner(
    mismatched_field: str,
) -> None:
    account_id = uuid4()
    idempotency_key = uuid4()
    service, repository, _ = make_service(account_id)
    repository.insert.return_value = None
    repository.find_by_idempotency_key.side_effect = [
        None,
        DeviceRegistration(
            uuid4(),
            uuid4() if mismatched_field == "account_id" else account_id,
            uuid4() if mismatched_field == "idempotency_key" else idempotency_key,
        ),
    ]

    with pytest.raises(ValueError, match="registration"):
        asyncio.run(
            service.create_device(
                account_id=account_id, idempotency_key=idempotency_key
            )
        )


def test_create_device_rolls_back_unresolved_conflict() -> None:
    account_id = uuid4()
    service, repository, _ = make_service(account_id)
    repository.insert.return_value = None

    with pytest.raises(RuntimeError, match="device conflict could not be resolved"):
        asyncio.run(
            service.create_device(account_id=account_id, idempotency_key=uuid4())
        )


def test_create_device_with_missing_account_remains_an_unhandled_failure() -> None:
    account_id = uuid4()
    service, _, accounts = make_service(account_id)
    accounts.find_by_id.return_value = None

    with pytest.raises(AccountNotFoundError):
        asyncio.run(
            service.create_device(account_id=account_id, idempotency_key=uuid4())
        )

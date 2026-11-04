import asyncio
from unittest.mock import create_autospec, sentinel
from uuid import uuid4

import pytest

from mosemo.identity.application.devices import RegisterDevice
from mosemo.identity.domain.devices import Device
from mosemo.identity.infrastructure.devices import DeviceRepository
from mosemo.shared.application.events import EventDispatcher
from tests.use_case_support import TransactionRecorder, record_transactions


@pytest.fixture
def transactions(monkeypatch: pytest.MonkeyPatch) -> TransactionRecorder:
    return record_transactions(
        monkeypatch, "mosemo.identity.application.devices", "unit_of_work"
    )


def make_service():
    repository = create_autospec(DeviceRepository, instance=True)
    service = RegisterDevice(devices=repository, dispatcher=EventDispatcher())
    return service, repository


def test_create_device_commits_new_device(transactions: TransactionRecorder) -> None:
    service, repository = make_service()
    account_id = uuid4()
    idempotency_key = uuid4()
    device = Device(
        id=uuid4(),
        account_id=account_id,
        idempotency_key=idempotency_key,
    )
    repository.get_or_create.return_value = (device, True)

    result = asyncio.run(
        service.execute(
            sentinel.db_session,
            account_id=account_id,
            idempotency_key=idempotency_key,
        )
    )

    assert result == device.id
    repository.get_or_create.assert_awaited_once()
    db_session, candidate = repository.get_or_create.await_args.args
    assert db_session is sentinel.db_session
    assert (candidate.account_id, candidate.idempotency_key) == (
        account_id,
        idempotency_key,
    )
    assert (transactions.entered, transactions.committed) == (1, 1)


def test_create_device_returns_original_id_for_retry(
    transactions: TransactionRecorder,
) -> None:
    service, repository = make_service()
    account_id = uuid4()
    idempotency_key = uuid4()
    device = Device(
        id=uuid4(),
        account_id=account_id,
        idempotency_key=idempotency_key,
    )
    repository.get_or_create.return_value = (device, False)

    result = asyncio.run(
        service.execute(
            sentinel.db_session,
            account_id=account_id,
            idempotency_key=idempotency_key,
        )
    )

    assert result == device.id
    assert (transactions.entered, transactions.committed) == (1, 1)


def test_create_device_rolls_back_unresolved_conflict(
    transactions: TransactionRecorder,
) -> None:
    service, repository = make_service()
    repository.get_or_create.side_effect = RuntimeError(
        "device conflict could not be resolved"
    )

    with pytest.raises(RuntimeError, match="device conflict could not be resolved"):
        asyncio.run(
            service.execute(
                sentinel.db_session,
                account_id=uuid4(),
                idempotency_key=uuid4(),
            )
        )

    assert (transactions.entered, transactions.committed) == (1, 0)

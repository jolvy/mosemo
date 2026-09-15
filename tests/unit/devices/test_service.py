import asyncio
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.devices.models import Device
from mosemo.devices.repository import DeviceRepository
from mosemo.devices.service import DeviceService


def make_service():
    session = create_autospec(AsyncSession, instance=True)
    repository = create_autospec(DeviceRepository, instance=True)
    service = DeviceService(session=session, repository=repository)
    return service, session, repository


def test_create_device_commits_new_device() -> None:
    service, session, repository = make_service()
    account_id = uuid4()
    idempotency_key = uuid4()
    device = Device(
        device_id=uuid4(),
        account_id=account_id,
        idempotency_key=idempotency_key,
    )
    repository.insert.return_value = device

    result = asyncio.run(
        service.create_device(
            account_id=account_id,
            idempotency_key=idempotency_key,
        )
    )

    assert result.device_id == device.device_id
    repository.insert.assert_awaited_once_with(
        account_id=account_id,
        idempotency_key=idempotency_key,
    )
    repository.find_by_idempotency_key.assert_not_awaited()
    session.begin.assert_called_once_with()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_create_device_returns_original_id_for_retry() -> None:
    service, session, repository = make_service()
    account_id = uuid4()
    idempotency_key = uuid4()
    device = Device(
        device_id=uuid4(),
        account_id=account_id,
        idempotency_key=idempotency_key,
    )
    repository.insert.return_value = None
    repository.find_by_idempotency_key.return_value = device

    result = asyncio.run(
        service.create_device(
            account_id=account_id,
            idempotency_key=idempotency_key,
        )
    )

    assert result.device_id == device.device_id
    repository.find_by_idempotency_key.assert_awaited_once_with(
        account_id=account_id,
        idempotency_key=idempotency_key,
    )
    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_create_device_rolls_back_unresolved_conflict() -> None:
    service, session, repository = make_service()
    repository.insert.return_value = None
    repository.find_by_idempotency_key.return_value = None

    with pytest.raises(RuntimeError, match="device conflict could not be resolved"):
        asyncio.run(
            service.create_device(
                account_id=uuid4(),
                idempotency_key=uuid4(),
            )
        )

    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()

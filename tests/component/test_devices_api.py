from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.api import v1_api_router
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.dependencies import get_device_service
from mosemo.devices.schemas import DeviceCreateResponse
from mosemo.devices.service import DeviceService
from mosemo.exception_handlers import register_exception_handlers


def make_app(
    *,
    config: Config,
    account_id: UUID,
    service: DeviceService,
) -> tuple[FastAPI, str]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_device_service] = lambda: service
    token = TokenService(config.auth).issue_access_token(account_id)
    return app, token


def test_devices_create_returns_server_id(config: Config) -> None:
    account_id = uuid4()
    service = create_autospec(DeviceService, instance=True)
    idempotency_key = uuid4()
    device_id = uuid4()
    service.create_device.return_value = DeviceCreateResponse(device_id=device_id)
    app, token = make_app(config=config, account_id=account_id, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/devices",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": str(idempotency_key),
            },
        )

    assert response.status_code == 201
    assert response.json() == {"deviceId": str(device_id)}
    service.create_device.assert_awaited_once_with(
        account_id=account_id,
        idempotency_key=idempotency_key,
    )


@pytest.mark.parametrize("idempotency_key", [None, "not-a-uuid"])
def test_devices_create_rejects_invalid_idempotency_key(
    config: Config,
    idempotency_key: str | None,
) -> None:
    account_id = uuid4()
    service = create_autospec(DeviceService, instance=True)
    app, token = make_app(config=config, account_id=account_id, service=service)
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key

    with TestClient(app) as client:
        response = client.post("/api/v1/devices", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"
    service.create_device.assert_not_awaited()


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_devices_create_requires_authentication(
    config: Config,
    authorization: str | None,
) -> None:
    account_id = uuid4()
    service = create_autospec(DeviceService, instance=True)
    app, _ = make_app(config=config, account_id=account_id, service=service)
    headers = {"Idempotency-Key": str(uuid4())}
    if authorization is not None:
        headers["Authorization"] = authorization

    with TestClient(app) as client:
        response = client.post("/api/v1/devices", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["status"] == "AUTH_INVALID_ACCESS_TOKEN"
    service.create_device.assert_not_awaited()

from unittest.mock import create_autospec, sentinel
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.identity.application.devices import RegisterDevice
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.identity.presentation.devices.router import create_router
from mosemo.platform.clock import utc_now
from tests.support.identity import token_service
from tests.support.identity_app import make_identity_app


def make_app(
    *,
    identity_config: IdentityConfig,
    account_id: UUID,
    service: RegisterDevice,
) -> tuple[FastAPI, str]:
    app = make_identity_app(
        create_router(register_device=service), identity_config=identity_config
    )
    token = token_service(identity_config).issue_access_token(account_id, now=utc_now())
    return app, token


def test_devices_create_returns_server_id(identity_config: IdentityConfig) -> None:
    account_id = uuid4()
    service = create_autospec(RegisterDevice, instance=True)
    idempotency_key = uuid4()
    device_id = uuid4()
    service.execute.return_value = device_id
    app, token = make_app(
        identity_config=identity_config, account_id=account_id, service=service
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/devices",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": str(idempotency_key),
            },
        )

    assert response.status_code == 201
    assert response.json() == {"id": str(device_id)}
    service.execute.assert_awaited_once_with(
        sentinel.db_session,
        account_id=account_id,
        idempotency_key=idempotency_key,
    )


@pytest.mark.parametrize("idempotency_key", [None, "not-a-uuid"])
def test_devices_create_rejects_invalid_idempotency_key(
    identity_config: IdentityConfig,
    idempotency_key: str | None,
) -> None:
    account_id = uuid4()
    service = create_autospec(RegisterDevice, instance=True)
    app, token = make_app(
        identity_config=identity_config, account_id=account_id, service=service
    )
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key

    with TestClient(app) as client:
        response = client.post("/api/v1/devices", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["status"] == "INVALID_ARGUMENT"
    service.execute.assert_not_awaited()


@pytest.mark.parametrize("authorization", [None, "Bearer invalid-token"])
def test_devices_create_requires_authentication(
    identity_config: IdentityConfig,
    authorization: str | None,
) -> None:
    account_id = uuid4()
    service = create_autospec(RegisterDevice, instance=True)
    app, _ = make_app(
        identity_config=identity_config, account_id=account_id, service=service
    )
    headers = {"Idempotency-Key": str(uuid4())}
    if authorization is not None:
        headers["Authorization"] = authorization

    with TestClient(app) as client:
        response = client.post("/api/v1/devices", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["status"] == "AUTH_INVALID_ACCESS_TOKEN"
    service.execute.assert_not_awaited()

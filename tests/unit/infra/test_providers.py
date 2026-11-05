from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from mosemo.entrypoints.api.contexts import IDENTITY
from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.entrypoints.api.main import app as api_app
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.platform.clock import utc_now
from mosemo.platform.providers import (
    get_clock,
    get_event_dispatcher,
    get_session,
)
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.presentation.current_account import (
    CurrentAccount,
    CurrentAccountDep,
    get_current_account,
)
from tests.identity_support import current_account_provider, token_service


def test_get_clock_returns_utc_now() -> None:
    assert get_clock() is utc_now


def test_get_event_dispatcher_returns_one_shared_dispatcher() -> None:
    dispatcher = get_event_dispatcher()

    assert isinstance(dispatcher, EventDispatcher)
    assert get_event_dispatcher() is dispatcher


@pytest.mark.asyncio
async def test_declared_current_account_provider_requires_override() -> None:
    with pytest.raises(NotImplementedError):
        await get_current_account(None)


@pytest.mark.asyncio
async def test_api_app_overrides_current_account_with_token_verification() -> None:
    provider = api_app.dependency_overrides[get_current_account]
    account_id = uuid4()
    token = IDENTITY.token_service.issue_access_token(account_id, now=utc_now())

    account = await provider(
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    )

    assert account == CurrentAccount(account_id=account_id)


def make_client(identity_config: IdentityConfig) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(account: CurrentAccountDep) -> dict[str, str]:
        assert isinstance(account, CurrentAccount)
        return {"accountId": str(account.account_id)}

    def no_database() -> None:
        raise AssertionError("the current account provider must not use a session")

    app.dependency_overrides[get_current_account] = current_account_provider(
        identity_config
    )
    app.dependency_overrides[get_session] = no_database
    return TestClient(app)


def test_current_account_comes_from_valid_token_without_session(
    identity_config: IdentityConfig,
) -> None:
    account_id = uuid4()
    token = token_service(identity_config).issue_access_token(account_id, now=utc_now())

    response = make_client(identity_config).get(
        "/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"accountId": str(account_id)}


@pytest.mark.parametrize("authorization", [None, "Bearer not-a-token", "expired"])
def test_current_account_rejects_missing_invalid_or_expired_token(
    identity_config: IdentityConfig, authorization: str | None
) -> None:
    if authorization == "expired":
        issued_at = datetime(2020, 1, 1, tzinfo=UTC) - timedelta(days=1)
        token = token_service(identity_config).issue_access_token(
            uuid4(), now=issued_at
        )
        authorization = f"Bearer {token}"
    headers = {} if authorization is None else {"Authorization": authorization}

    response = make_client(identity_config).get("/me", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["status"] == "AUTH_INVALID_ACCESS_TOKEN"

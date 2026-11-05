from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.entrypoints.api.exception_handlers import register_exception_handlers
from mosemo.entrypoints.api.main import app as api_app
from mosemo.identity.bootstrap import get_token_current_account
from mosemo.identity.presentation.auth.router import get_token_service
from mosemo.platform.clock import utc_now
from mosemo.platform.config import Config
from mosemo.platform.providers import (
    get_clock,
    get_config,
    get_event_dispatcher,
    get_session,
)
from mosemo.shared.application.events import EventDispatcher
from mosemo.shared.presentation.current_account import (
    CurrentAccount,
    CurrentAccountDep,
    get_current_account,
)


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


def test_api_app_overrides_current_account_with_token_verification() -> None:
    assert api_app.dependency_overrides[get_current_account] is (
        get_token_current_account
    )


def make_client(config: Config) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(account: CurrentAccountDep) -> dict[str, str]:
        assert isinstance(account, CurrentAccount)
        return {"accountId": str(account.account_id)}

    def no_database() -> None:
        raise AssertionError("the current account provider must not use a session")

    app.dependency_overrides[get_current_account] = get_token_current_account
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_session] = no_database
    return TestClient(app)


def test_current_account_comes_from_valid_token_without_session(
    config: Config,
) -> None:
    account_id = uuid4()
    token = get_token_service(config).issue_access_token(account_id, now=utc_now())

    response = make_client(config).get(
        "/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"accountId": str(account_id)}


@pytest.mark.parametrize("authorization", [None, "Bearer not-a-token", "expired"])
def test_current_account_rejects_missing_invalid_or_expired_token(
    config: Config, authorization: str | None
) -> None:
    if authorization == "expired":
        issued_at = datetime(2020, 1, 1, tzinfo=UTC) - timedelta(days=1)
        token = get_token_service(config).issue_access_token(uuid4(), now=issued_at)
        authorization = f"Bearer {token}"
    headers = {} if authorization is None else {"Authorization": authorization}

    response = make_client(config).get("/me", headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["status"] == "AUTH_INVALID_ACCESS_TOKEN"

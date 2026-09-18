from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import create_autospec
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.service import AccountNotFoundError, AccountService
from mosemo.api import v1_api_router
from mosemo.auth.service import AuthService
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.dependencies import get_account_service, get_auth_service
from mosemo.exception_handlers import register_exception_handlers
from mosemo.timezones import Timezone


def make_account() -> Account:
    now = datetime.now(UTC)
    return Account(
        account_id=uuid4(),
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
        timezone=Timezone.ASIA_SEOUL,
        created_at=now,
        last_authenticated_at=now,
    )


def make_app(
    *,
    config: Config,
    account_service: AccountService,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_auth_service] = lambda: create_autospec(
        AuthService,
        instance=True,
    )
    app.dependency_overrides[get_account_service] = lambda: account_service
    return app


def test_accounts_me_returns_authenticated_account(config: Config) -> None:
    account = make_account()
    korean_standard_time = timezone(timedelta(hours=9))
    account.created_at = datetime(
        2026,
        9,
        5,
        10,
        2,
        3,
        123456,
        tzinfo=korean_standard_time,
    )
    account.last_authenticated_at = datetime(
        2026,
        9,
        5,
        10,
        2,
        4,
        987654,
        tzinfo=korean_standard_time,
    )
    account_service = create_autospec(AccountService, instance=True)
    account_service.get_account.return_value = account
    app = make_app(config=config, account_service=account_service)
    access_token = TokenService(config.auth).issue_access_token(account.account_id)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/accounts/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "accountId": str(account.account_id),
        "provider": "KAKAO",
        "timezone": "Asia/Seoul",
        "createdAt": "2026-09-05T01:02:03Z",
        "lastAuthenticatedAt": "2026-09-05T01:02:04Z",
    }
    assert "provider_subject" not in response.json()
    account_service.get_account.assert_awaited_once_with(account.account_id)


def test_accounts_me_rejects_missing_or_invalid_bearer(config: Config) -> None:
    account_service = create_autospec(AccountService, instance=True)
    app = make_app(config=config, account_service=account_service)

    with TestClient(app) as client:
        missing_response = client.get("/api/v1/accounts/me")
        invalid_response = client.get(
            "/api/v1/accounts/me",
            headers={"Authorization": "Bearer invalid-token"},
        )

    for response in (missing_response, invalid_response):
        assert response.status_code == 401
        assert response.json() == {
            "error": {
                "status": "AUTH_INVALID_ACCESS_TOKEN",
                "code": 401,
                "message": "Invalid or expired access token",
                "details": [],
            }
        }
        assert response.headers["www-authenticate"] == "Bearer"
    account_service.get_account.assert_not_awaited()


def test_accounts_me_rejects_deleted_account(config: Config) -> None:
    account_id = uuid4()
    account_service = create_autospec(AccountService, instance=True)
    account_service.get_account.side_effect = AccountNotFoundError()
    app = make_app(config=config, account_service=account_service)
    access_token = TokenService(config.auth).issue_access_token(account_id)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/accounts/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "status": "AUTH_INVALID_ACCESS_TOKEN",
            "code": 401,
            "message": "Invalid or expired access token",
            "details": [],
        }
    }
    assert response.headers["www-authenticate"] == "Bearer"
    account_service.get_account.assert_awaited_once_with(account_id)

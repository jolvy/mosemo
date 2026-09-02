from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.api import v1_api_router
from mosemo.auth.service import AuthService
from mosemo.auth.tokens import TokenService
from mosemo.config import Config, get_config
from mosemo.dependencies import get_account_repository, get_auth_service


def make_account() -> Account:
    now = datetime.now(UTC)
    return Account(
        account_id=uuid4(),
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
        created_at=now,
        last_authenticated_at=now,
    )


def make_app(
    *,
    config: Config,
    account_repository: AccountRepository,
) -> FastAPI:
    app = FastAPI()
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_auth_service] = lambda: create_autospec(
        AuthService,
        instance=True,
    )
    app.dependency_overrides[get_account_repository] = lambda: account_repository
    return app


def test_accounts_me_returns_authenticated_account(config: Config) -> None:
    account = make_account()
    account_repository = create_autospec(AccountRepository, instance=True)
    account_repository.find_by_id.return_value = account
    app = make_app(config=config, account_repository=account_repository)
    access_token = TokenService(config.auth).issue_access_token(account.account_id)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/accounts/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "account_id": str(account.account_id),
        "provider": "KAKAO",
        "created_at": account.created_at.isoformat().replace("+00:00", "Z"),
        "last_authenticated_at": account.last_authenticated_at.isoformat().replace(
            "+00:00", "Z"
        ),
    }
    assert "provider_subject" not in response.json()
    account_repository.find_by_id.assert_awaited_once_with(account.account_id)


def test_accounts_me_rejects_missing_or_invalid_bearer(config: Config) -> None:
    account_repository = create_autospec(AccountRepository, instance=True)
    app = make_app(config=config, account_repository=account_repository)

    with TestClient(app) as client:
        missing_response = client.get("/api/v1/accounts/me")
        invalid_response = client.get(
            "/api/v1/accounts/me",
            headers={"Authorization": "Bearer invalid-token"},
        )

    for response in (missing_response, invalid_response):
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid or expired access token"}
        assert response.headers["www-authenticate"] == "Bearer"
    account_repository.find_by_id.assert_not_awaited()


def test_accounts_me_rejects_deleted_account(config: Config) -> None:
    account_id = uuid4()
    account_repository = create_autospec(AccountRepository, instance=True)
    account_repository.find_by_id.return_value = None
    app = make_app(config=config, account_repository=account_repository)
    access_token = TokenService(config.auth).issue_access_token(account_id)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/accounts/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    account_repository.find_by_id.assert_awaited_once_with(account_id)

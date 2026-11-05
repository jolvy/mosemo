from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import create_autospec, sentinel
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.identity.application.accounts import AccountNotFoundError, GetAccount
from mosemo.identity.bootstrap import IdentityConfig
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.presentation.accounts.router import create_router
from mosemo.platform.clock import utc_now
from mosemo.shared.domain.timezones import Timezone
from tests.support.identity import token_service
from tests.support.identity_app import make_identity_app


def make_account() -> Account:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    account = Account(
        id=uuid4(),
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    account.timezone = Timezone.ASIA_SEOUL
    account.created_at = now
    account.last_authenticated_at = now
    return account


def make_app(
    *,
    identity_config: IdentityConfig,
    account_service: GetAccount,
) -> FastAPI:
    return make_identity_app(
        create_router(get_account=account_service), identity_config=identity_config
    )


def test_accounts_me_returns_authenticated_account(
    identity_config: IdentityConfig,
) -> None:
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
    account_service = create_autospec(GetAccount, instance=True)
    account_service.execute.return_value = account
    app = make_app(identity_config=identity_config, account_service=account_service)
    access_token = token_service(identity_config).issue_access_token(
        account.id, now=utc_now()
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/accounts/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "id": str(account.id),
        "provider": "KAKAO",
        "timezone": "Asia/Seoul",
        "createdAt": "2026-09-05T01:02:03Z",
        "lastAuthenticatedAt": "2026-09-05T01:02:04Z",
    }
    assert "provider_subject" not in response.json()
    account_service.execute.assert_awaited_once_with(sentinel.db_session, account.id)


def test_accounts_me_rejects_missing_or_invalid_bearer(
    identity_config: IdentityConfig,
) -> None:
    account_service = create_autospec(GetAccount, instance=True)
    app = make_app(identity_config=identity_config, account_service=account_service)

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
    account_service.execute.assert_not_awaited()


def test_accounts_me_rejects_deleted_account(identity_config: IdentityConfig) -> None:
    account_id = uuid4()
    account_service = create_autospec(GetAccount, instance=True)
    account_service.execute.side_effect = AccountNotFoundError()
    app = make_app(identity_config=identity_config, account_service=account_service)
    access_token = token_service(identity_config).issue_access_token(
        account_id, now=utc_now()
    )

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
    account_service.execute.assert_awaited_once_with(sentinel.db_session, account_id)

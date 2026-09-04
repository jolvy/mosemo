from datetime import UTC, datetime
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.accounts.models import Account, AccountProvider
from mosemo.api import v1_api_router
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.router import KAKAO_AUTHORIZE_URL
from mosemo.auth.service import AuthService, InvalidAuthorizationCodeError
from mosemo.config import Config, get_config
from mosemo.dependencies import get_auth_service
from mosemo.exception_handlers import register_exception_handlers

CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)


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
    service: AuthService,
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_auth_service] = lambda: service
    return app


def test_login_callback_and_token_exchange_flow(
    config: Config,
    monkeypatch,
) -> None:
    account = make_account()
    service = create_autospec(AuthService, instance=True)
    service.authenticate_kakao.return_value = account
    service.create_authorization_code.return_value = "one-time-code"
    service.exchange_authorization_code.return_value = "access-token"
    monkeypatch.setattr(
        "mosemo.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )
    app = make_app(config=config, service=service)

    with TestClient(app) as client:
        login_response = client.get(
            "/api/v1/auth/kakao/login",
            params={
                "code_challenge": CODE_CHALLENGE,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        callback_response = client.get(
            "/api/v1/auth/kakao/callback",
            params={"code": "authorization-code", "state": "fixed-state"},
            follow_redirects=False,
        )
        token_response = client.post(
            "/api/v1/auth/token",
            json={
                "grant_type": "authorization_code",
                "code": "one-time-code",
                "code_verifier": CODE_VERIFIER,
            },
        )

    assert login_response.status_code == 302
    assert login_response.headers["location"].startswith(KAKAO_AUTHORIZE_URL)
    assert callback_response.status_code == 302
    assert callback_response.headers["location"] == (
        "com.example.mosemo:/auth/callback?code=one-time-code"
    )
    assert token_response.status_code == 200
    assert token_response.json() == {
        "access_token": "access-token",
        "token_type": "Bearer",
        "expires_in": 86_400,
    }
    assert token_response.headers["cache-control"] == "no-store"
    assert token_response.headers["pragma"] == "no-cache"
    service.authenticate_kakao.assert_awaited_once_with(code="authorization-code")
    service.create_authorization_code.assert_awaited_once_with(
        account=account,
        code_challenge=CODE_CHALLENGE,
    )
    service.exchange_authorization_code.assert_awaited_once_with(
        authorization_code="one-time-code",
        code_verifier=CODE_VERIFIER,
    )


def test_callback_without_state_cookie_returns_bad_request(config: Config) -> None:
    service = create_autospec(AuthService, instance=True)
    app = make_app(config=config, service=service)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/kakao/callback",
            params={"code": "authorization-code", "state": "missing-cookie"},
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Kakao OAuth state"}
    service.authenticate_kakao.assert_not_awaited()


def test_token_exchange_returns_documented_bad_request(config: Config) -> None:
    service = create_autospec(AuthService, instance=True)
    service.exchange_authorization_code.side_effect = InvalidAuthorizationCodeError()
    app = make_app(config=config, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/token",
            json={
                "grant_type": "authorization_code",
                "code": "invalid-code",
                "code_verifier": CODE_VERIFIER,
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Invalid or expired authorization code",
    }


@pytest.mark.parametrize(
    "params",
    [
        {},
        {
            "code_challenge": "short",
            "code_challenge_method": "S256",
        },
        {
            "code_challenge": CODE_CHALLENGE,
            "code_challenge_method": "plain",
        },
    ],
)
def test_login_rejects_missing_or_invalid_pkce(
    config: Config,
    params: dict[str, str],
) -> None:
    service = create_autospec(AuthService, instance=True)
    app = make_app(config=config, service=service)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/kakao/login",
            params=params,
            follow_redirects=False,
        )

    assert response.status_code == 422

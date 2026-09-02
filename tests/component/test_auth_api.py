from unittest.mock import create_autospec

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.api import v1_api_router
from mosemo.auth.router import KAKAO_AUTHORIZE_URL
from mosemo.auth.service import AuthService
from mosemo.config import Config, get_config
from mosemo.dependencies import get_auth_service


def make_app(*, config: Config, service: AuthService) -> FastAPI:
    app = FastAPI()
    app.include_router(v1_api_router)
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_auth_service] = lambda: service
    return app


def test_login_then_callback_uses_state_cookie(
    config: Config,
    monkeypatch,
) -> None:
    existing_account = {
        "provider": "KAKAO",
        "provider_subject": "123456789",
    }
    service = create_autospec(AuthService, instance=True)
    service.authenticate_kakao.return_value = existing_account
    monkeypatch.setattr(
        "mosemo.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )
    app = make_app(config=config, service=service)

    with TestClient(app) as client:
        login_response = client.get(
            "/api/v1/auth/kakao/login",
            follow_redirects=False,
        )
        callback_response = client.get(
            "/api/v1/auth/kakao/callback",
            params={"code": "authorization-code", "state": "fixed-state"},
        )

    assert login_response.status_code == 302
    assert login_response.headers["location"].startswith(KAKAO_AUTHORIZE_URL)
    assert callback_response.status_code == 200
    assert callback_response.json() == existing_account
    service.authenticate_kakao.assert_awaited_once_with(code="authorization-code")


def test_callback_without_state_cookie_returns_bad_request(config: Config) -> None:
    service = create_autospec(AuthService, instance=True)
    app = make_app(config=config, service=service)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/kakao/callback",
            params={"code": "authorization-code", "state": "missing-cookie"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Kakao OAuth state"}
    service.authenticate_kakao.assert_not_awaited()

from types import SimpleNamespace
from typing import cast
from unittest.mock import ANY, Mock, create_autospec, sentinel

import httpx2
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from mosemo.entrypoints.api.config import ApiConfig
from mosemo.identity.application.sign_in import (
    ExchangeAuthorizationCode,
    InvalidAuthorizationCodeError,
    LogIn,
)
from mosemo.identity.bootstrap import (
    IdentityConfig,
    create_kakao_client,
)
from mosemo.identity.domain.accounts import AccountProvider
from mosemo.identity.domain.pkce import create_code_challenge
from mosemo.identity.infrastructure.kakao_client import KAKAO_AUTHORIZE_URL
from mosemo.identity.presentation.auth.router import (
    create_oauth_router,
    create_token_router,
)
from tests.component.identity_app import make_identity_app
from tests.identity_support import token_service

CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)


def make_service() -> SimpleNamespace:
    return SimpleNamespace(
        log_in=create_autospec(LogIn, instance=True),
        exchange=create_autospec(ExchangeAuthorizationCode, instance=True),
    )


def make_app(
    *,
    identity_config: IdentityConfig,
    service: SimpleNamespace,
) -> FastAPI:
    router = APIRouter()
    router.include_router(
        create_oauth_router(
            log_in=service.log_in,
            get_login_client=lambda: create_kakao_client(
                identity_config, http_client=cast(httpx2.AsyncClient, object())
            ),
            get_oauth_clients=lambda: Mock(),
            macos_callback_uri=identity_config.macos_callback_uri,
            secure_cookies=identity_config.secure_cookies,
        )
    )
    router.include_router(
        create_token_router(
            exchange_authorization_code=service.exchange,
            token_service=token_service(identity_config),
            access_token_ttl_seconds=identity_config.access_token_ttl_seconds,
        )
    )
    return make_identity_app(router, identity_config=identity_config, prefix="/auth")


def test_login_callback_and_token_exchange_flow(
    identity_config: IdentityConfig,
    monkeypatch,
) -> None:
    service = make_service()
    service.log_in.execute.return_value = "one-time-code"
    service.exchange.execute.return_value = "access-token"
    monkeypatch.setattr(
        "mosemo.identity.presentation.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )
    app = make_app(identity_config=identity_config, service=service)

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
                "grantType": "authorization_code",
                "code": "one-time-code",
                "codeVerifier": CODE_VERIFIER,
            },
        )

    assert login_response.status_code == 302
    assert login_response.content == b""
    assert "content-type" not in login_response.headers
    assert login_response.headers["location"].startswith(KAKAO_AUTHORIZE_URL)
    assert login_response.headers["cache-control"] == "no-store"
    assert login_response.headers["pragma"] == "no-cache"
    assert "set-cookie" in login_response.headers
    assert callback_response.status_code == 302
    assert callback_response.content == b""
    assert "content-type" not in callback_response.headers
    assert callback_response.headers["location"] == (
        "com.example.mosemo:/auth/callback?code=one-time-code"
    )
    assert callback_response.headers["cache-control"] == "no-store"
    assert callback_response.headers["pragma"] == "no-cache"
    assert "set-cookie" in callback_response.headers
    assert token_response.status_code == 200
    assert token_response.headers["content-type"] == "application/json"
    assert token_response.json() == {
        "accessToken": "access-token",
        "tokenType": "Bearer",
        "expiresIn": 86_400,
    }
    assert token_response.headers["cache-control"] == "no-store"
    assert token_response.headers["pragma"] == "no-cache"
    service.log_in.execute.assert_awaited_once_with(
        sentinel.db_session,
        oauth_clients=ANY,
        provider=AccountProvider.KAKAO,
        code="authorization-code",
        code_challenge=CODE_CHALLENGE,
    )
    service.exchange.execute.assert_awaited_once_with(
        sentinel.db_session,
        authorization_code="one-time-code",
        code_verifier=CODE_VERIFIER,
        token_service=ANY,
    )


@pytest.mark.parametrize(("app_env", "secure"), [("prod", True), ("test", False)])
def test_oauth_cookies_are_secure_only_in_prod(
    api_config: ApiConfig, app_env: str, *, secure: bool
) -> None:
    identity_config = api_config.model_copy(update={"app_env": app_env}).identity()
    app = make_app(identity_config=identity_config, service=make_service())

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
            "/api/v1/auth/kakao/callback", follow_redirects=False
        )

    for response in (login_response, callback_response):
        cookies = response.headers.get_list("set-cookie")
        assert len(cookies) == 2
        assert all(("; secure" in cookie.lower()) is secure for cookie in cookies)


def test_callback_without_state_cookie_returns_bad_request(
    identity_config: IdentityConfig,
) -> None:
    service = make_service()
    app = make_app(identity_config=identity_config, service=service)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/kakao/callback",
            params={"code": "authorization-code", "state": "missing-cookie"},
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "status": "AUTH_INVALID_OAUTH_CONTEXT",
            "code": 400,
            "message": "Invalid or expired OAuth login context",
            "details": [],
        }
    }
    service.log_in.execute.assert_not_awaited()


def test_callback_maps_kakao_error_query_without_exposing_description(
    identity_config: IdentityConfig,
    monkeypatch,
) -> None:
    service = make_service()
    monkeypatch.setattr(
        "mosemo.identity.presentation.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )
    app = make_app(identity_config=identity_config, service=service)

    with TestClient(app) as client:
        client.get(
            "/api/v1/auth/kakao/login",
            params={
                "code_challenge": CODE_CHALLENGE,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        response = client.get(
            "/api/v1/auth/kakao/callback",
            params={
                "state": "fixed-state",
                "error": "access_denied",
                "error_description": "sensitive provider description",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "com.example.mosemo:/auth/callback?error=access_denied"
    )
    assert "sensitive provider description" not in response.text
    service.log_in.execute.assert_not_awaited()


def test_callback_without_code_redirects_authentication_failure(
    identity_config: IdentityConfig,
    monkeypatch,
) -> None:
    service = make_service()
    monkeypatch.setattr(
        "mosemo.identity.presentation.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )
    app = make_app(identity_config=identity_config, service=service)

    with TestClient(app) as client:
        client.get(
            "/api/v1/auth/kakao/login",
            params={
                "code_challenge": CODE_CHALLENGE,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        response = client.get(
            "/api/v1/auth/kakao/callback",
            params={"state": "fixed-state"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "com.example.mosemo:/auth/callback?error=authentication_failed"
    )
    service.log_in.execute.assert_not_awaited()


def test_token_exchange_returns_documented_bad_request(
    identity_config: IdentityConfig,
) -> None:
    service = make_service()
    service.exchange.execute.side_effect = InvalidAuthorizationCodeError()
    app = make_app(identity_config=identity_config, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/token",
            json={
                "grantType": "authorization_code",
                "code": "invalid-code",
                "codeVerifier": CODE_VERIFIER,
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "status": "AUTH_INVALID_AUTHORIZATION_CODE",
            "code": 400,
            "message": "Invalid or expired authorization code",
            "details": [],
        }
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
    identity_config: IdentityConfig,
    params: dict[str, str],
) -> None:
    service = make_service()
    app = make_app(identity_config=identity_config, service=service)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/auth/kakao/login",
            params=params,
            follow_redirects=False,
        )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == 422
    assert error["status"] == "INVALID_ARGUMENT"
    assert error["message"] == "Request validation failed."
    assert error["details"]
    assert all(set(detail) == {"loc", "msg", "type"} for detail in error["details"])


def test_token_validation_does_not_expose_input_or_framework_metadata(
    identity_config: IdentityConfig,
) -> None:
    service = make_service()
    app = make_app(identity_config=identity_config, service=service)

    with TestClient(app) as client:
        snake_case_response = client.post(
            "/api/v1/auth/token",
            json={
                "grant_type": "authorization_code",
                "code": "secret-code",
                "codeVerifier": CODE_VERIFIER,
            },
        )
        malformed_json_response = client.post(
            "/api/v1/auth/token",
            content="{malformed-secret",
            headers={"content-type": "application/json"},
        )

    for response in (snake_case_response, malformed_json_response):
        assert response.status_code == 422
        payload = response.json()
        assert set(payload) == {"error"}
        assert payload["error"]["status"] == "INVALID_ARGUMENT"
        assert payload["error"]["details"]
        serialized = response.text
        assert "secret-code" not in serialized
        assert "pydantic" not in serialized.lower()
        assert '"input"' not in serialized.lower()
        assert '"ctx"' not in serialized.lower()
        assert "errors.pydantic.dev" not in serialized

    snake_locations = {
        tuple(detail["loc"])
        for detail in snake_case_response.json()["error"]["details"]
    }
    assert ("body", "grantType") in snake_locations
    assert ("body", "grant_type") in snake_locations


@pytest.mark.parametrize(
    "body", [b"{", b'{"grantType":', b'{"code":"secret-value"} trailing']
)
def test_token_body_parsing_errors_follow_framework_detail(
    identity_config: IdentityConfig,
    body: bytes,
) -> None:
    service = make_service()
    app = make_app(identity_config=identity_config, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/token",
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["status"] == "INVALID_ARGUMENT"
    assert error["code"] == 422
    assert error["message"] == "Request validation failed."
    assert len(error["details"]) == 1
    detail = error["details"][0]
    assert detail["loc"][0] == "body"
    assert isinstance(detail["loc"][1], int)
    assert detail["msg"] == "JSON decode error"
    assert detail["type"] == "json_invalid"
    service.exchange.execute.assert_not_awaited()


@pytest.mark.parametrize("body", [b"\xff", b"\xff\xfe{"])
def test_token_invalid_encoding_uses_server_detail_type(
    identity_config: IdentityConfig,
    body: bytes,
) -> None:
    service = make_service()
    app = make_app(identity_config=identity_config, service=service)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/token",
            content=body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["details"] == [
        {
            "loc": ["body"],
            "msg": "Invalid request body encoding",
            "type": "invalid_encoding",
        }
    ]


@pytest.mark.parametrize("include_public_alias", [True, False])
def test_token_unexpected_fields_point_to_the_containing_object(
    identity_config: IdentityConfig,
    include_public_alias: bool,
) -> None:
    service = make_service()
    app = make_app(identity_config=identity_config, service=service)
    body = {
        "grantType": "authorization_code",
        "code": "secret-code",
        "code_verifier": "secret-verifier",
    }
    if include_public_alias:
        body["codeVerifier"] = CODE_VERIFIER

    with TestClient(app) as client:
        response = client.post("/api/v1/auth/token", json=body)

    assert response.status_code == 422
    details = response.json()["error"]["details"]
    expected = {("extra_forbidden", ("body", "code_verifier"))}
    if not include_public_alias:
        expected.add(("missing", ("body", "codeVerifier")))
    assert {(detail["type"], tuple(detail["loc"])) for detail in details} == expected
    assert all(detail["loc"][0] == "body" for detail in details)
    assert "code_verifier" in response.text
    assert "secret-code" not in response.text
    assert "secret-verifier" not in response.text
    service.exchange.execute.assert_not_awaited()

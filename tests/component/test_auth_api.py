from unittest.mock import create_autospec

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mosemo.api import v1_api_router
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.router import KAKAO_AUTHORIZE_URL
from mosemo.auth.service import AuthService, InvalidAuthorizationCodeError
from mosemo.config import Config, get_config
from mosemo.dependencies import get_auth_service
from mosemo.exception_handlers import register_exception_handlers

CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)


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
    service = create_autospec(AuthService, instance=True)
    service.complete_kakao_login.return_value = "one-time-code"
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
    service.complete_kakao_login.assert_awaited_once_with(
        code="authorization-code",
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
    assert response.json() == {
        "error": {
            "status": "AUTH_INVALID_OAUTH_CONTEXT",
            "code": 400,
            "message": "Invalid or expired OAuth login context",
            "details": [],
        }
    }
    service.complete_kakao_login.assert_not_awaited()


def test_token_exchange_returns_documented_bad_request(config: Config) -> None:
    service = create_autospec(AuthService, instance=True)
    service.exchange_authorization_code.side_effect = InvalidAuthorizationCodeError()
    app = make_app(config=config, service=service)

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
    error = response.json()["error"]
    assert error["code"] == 422
    assert error["status"] == "INVALID_ARGUMENT"
    assert error["message"] == "Request validation failed."
    assert error["details"]
    assert all(set(detail) == {"loc", "msg", "type"} for detail in error["details"])


def test_token_validation_does_not_expose_input_or_framework_metadata(
    config: Config,
) -> None:
    service = create_autospec(AuthService, instance=True)
    app = make_app(config=config, service=service)

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
    config: Config,
    body: bytes,
) -> None:
    service = create_autospec(AuthService, instance=True)
    app = make_app(config=config, service=service)

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
    service.exchange_authorization_code.assert_not_awaited()


@pytest.mark.parametrize("body", [b"\xff", b"\xff\xfe{"])
def test_token_invalid_encoding_uses_server_detail_type(
    config: Config,
    body: bytes,
) -> None:
    service = create_autospec(AuthService, instance=True)
    app = make_app(config=config, service=service)

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
    config: Config,
    include_public_alias: bool,
) -> None:
    service = create_autospec(AuthService, instance=True)
    app = make_app(config=config, service=service)
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
    service.exchange_authorization_code.assert_not_awaited()

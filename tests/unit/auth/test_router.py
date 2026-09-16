import asyncio
import json
from http.cookies import SimpleCookie
from typing import cast
from unittest.mock import create_autospec
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi import Response

from mosemo.auth.kakao_client import KAKAO_AUTHORIZE_URL, KakaoClient
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.router import (
    PKCE_CHALLENGE_COOKIE,
    STATE_COOKIE,
    callback,
    exchange_token,
    login,
)
from mosemo.auth.schemas import TokenRequest
from mosemo.auth.service import (
    AuthService,
    InvalidAuthorizationCodeError,
    OAuthAuthenticationError,
)
from mosemo.config import Config
from mosemo.exceptions import ApiException

CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)


def make_service():
    return create_autospec(AuthService, instance=True)


def make_kakao_client(config: Config) -> KakaoClient:
    return KakaoClient(
        http_client=cast(httpx2.AsyncClient, object()),
        config=config.kakao,
    )


def response_cookies(response) -> SimpleCookie:
    cookies = SimpleCookie()
    for header in response.headers.getlist("set-cookie"):
        cookies.load(header)
    return cookies


def test_login_redirects_to_kakao_and_sets_oauth_cookies(
    config: Config,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "mosemo.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )

    response = login(config, make_kakao_client(config), CODE_CHALLENGE, "S256")

    location = urlsplit(response.headers["location"])
    query = parse_qs(location.query)
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        KAKAO_AUTHORIZE_URL
    )
    assert query == {
        "client_id": ["test-rest-api-key"],
        "redirect_uri": ["http://localhost:8000/api/v1/auth/kakao/callback"],
        "response_type": ["code"],
        "state": ["fixed-state"],
    }

    cookies = response_cookies(response)
    assert cookies[STATE_COOKIE].value == "fixed-state"
    assert cookies[PKCE_CHALLENGE_COOKIE].value == CODE_CHALLENGE
    for key in (STATE_COOKIE, PKCE_CHALLENGE_COOKIE):
        assert cookies[key]["max-age"] == "600"
        assert cookies[key]["httponly"] is True
        assert cookies[key]["samesite"] == "lax"
        assert cookies[key]["path"] == "/api/v1/auth/kakao"
        assert cookies[key]["secure"] == ""


def test_login_uses_secure_cookies_in_production(
    config: Config,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "mosemo.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )
    production_config = config.model_copy(update={"app_env": "prod"})

    response = login(
        production_config,
        make_kakao_client(production_config),
        CODE_CHALLENGE,
        "S256",
    )

    cookies = response_cookies(response)
    assert cookies[STATE_COOKIE]["secure"] is True
    assert cookies[PKCE_CHALLENGE_COOKIE]["secure"] is True


@pytest.mark.parametrize(
    ("state", "state_cookie", "pkce_challenge_cookie"),
    [
        (None, "state", CODE_CHALLENGE),
        ("state", None, CODE_CHALLENGE),
        ("state", "different-state", CODE_CHALLENGE),
        ("state", "state", None),
        ("state", "state", "tampered-challenge"),
    ],
)
def test_callback_rejects_invalid_state_and_clears_cookies(
    config: Config,
    state: str | None,
    state_cookie: str | None,
    pkce_challenge_cookie: str | None,
) -> None:
    service = make_service()

    response = asyncio.run(
        callback(
            service=service,
            config=config,
            code="authorization-code",
            state=state,
            state_cookie=state_cookie,
            pkce_challenge_cookie=pkce_challenge_cookie,
            error=None,
            error_description=None,
        )
    )

    assert response.status_code == 400
    assert json.loads(response.body) == {
        "error": {
            "status": "AUTH_INVALID_OAUTH_CONTEXT",
            "code": 400,
            "message": "Invalid or expired OAuth login context",
            "details": [],
        }
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    cookies = response_cookies(response)
    assert cookies[STATE_COOKIE]["max-age"] == "0"
    assert cookies[PKCE_CHALLENGE_COOKIE]["max-age"] == "0"
    service.login.assert_not_awaited()


def test_callback_redirects_authorization_code_to_macos_app(
    config: Config,
) -> None:
    service = make_service()
    service.login.return_value = "one-time-code"

    response = asyncio.run(
        callback(
            service=service,
            config=config,
            code="authorization-code",
            state="valid-state",
            state_cookie="valid-state",
            pkce_challenge_cookie=CODE_CHALLENGE,
            error=None,
            error_description=None,
        )
    )

    location = urlsplit(response.headers["location"])
    assert response.status_code == 302
    assert location.scheme == "com.example.mosemo"
    assert location.path == "/auth/callback"
    assert parse_qs(location.query) == {"code": ["one-time-code"]}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    service.login.assert_awaited_once_with(
        code="authorization-code",
        code_challenge=CODE_CHALLENGE,
    )
    cookies = response_cookies(response)
    assert cookies[STATE_COOKIE]["max-age"] == "0"
    assert cookies[PKCE_CHALLENGE_COOKIE]["max-age"] == "0"


def test_callback_redirects_authentication_failure_to_macos_app(
    config: Config,
) -> None:
    service = make_service()
    service.login.side_effect = OAuthAuthenticationError()

    response = asyncio.run(
        callback(
            service=service,
            config=config,
            code="authorization-code",
            state="valid-state",
            state_cookie="valid-state",
            pkce_challenge_cookie=CODE_CHALLENGE,
            error=None,
            error_description=None,
        )
    )

    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "error": ["authentication_failed"]
    }
    service.login.assert_awaited_once_with(
        code="authorization-code",
        code_challenge=CODE_CHALLENGE,
    )


@pytest.mark.parametrize(
    ("provider_error", "public_error"),
    [
        ("access_denied", "access_denied"),
        ("temporarily_unavailable", "authentication_failed"),
    ],
)
def test_callback_maps_provider_error_without_authenticating(
    config: Config,
    provider_error: str,
    public_error: str,
) -> None:
    service = make_service()

    response = asyncio.run(
        callback(
            service=service,
            config=config,
            code=None,
            state="valid-state",
            state_cookie="valid-state",
            pkce_challenge_cookie=CODE_CHALLENGE,
            error=provider_error,
            error_description="sensitive provider description",
        )
    )

    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "error": [public_error]
    }
    service.login.assert_not_awaited()


def test_exchange_token_returns_bearer_response(config: Config) -> None:
    service = make_service()
    service.exchange_authorization_code.return_value = "access-token"

    result = asyncio.run(
        exchange_token(
            TokenRequest.model_validate(
                {
                    "grantType": "authorization_code",
                    "code": "one-time-code",
                    "codeVerifier": CODE_VERIFIER,
                }
            ),
            service,
            config,
            Response(),
        )
    )

    assert result.model_dump() == {
        "accessToken": "access-token",
        "tokenType": "Bearer",
        "expiresIn": 86_400,
    }
    service.exchange_authorization_code.assert_awaited_once_with(
        authorization_code="one-time-code",
        code_verifier=CODE_VERIFIER,
    )


def test_exchange_token_returns_fixed_error(config: Config) -> None:
    service = make_service()
    service.exchange_authorization_code.side_effect = InvalidAuthorizationCodeError()

    with pytest.raises(ApiException) as exc_info:
        asyncio.run(
            exchange_token(
                TokenRequest.model_validate(
                    {
                        "grantType": "authorization_code",
                        "code": "invalid-code",
                        "codeVerifier": CODE_VERIFIER,
                    }
                ),
                service,
                config,
                Response(),
            )
        )

    assert exc_info.value.spec.status == "AUTH_INVALID_AUTHORIZATION_CODE"
    assert exc_info.value.spec.code == 400
    assert exc_info.value.spec.message == "Invalid or expired authorization code"
    assert isinstance(exc_info.value.__cause__, InvalidAuthorizationCodeError)
    service.exchange_authorization_code.assert_awaited_once_with(
        authorization_code="invalid-code",
        code_verifier=CODE_VERIFIER,
    )

import asyncio
import dataclasses
import json
from http.cookies import SimpleCookie
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, create_autospec, sentinel
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi import Response
from fastapi.routing import APIRoute

from mosemo.identity.application.sign_in import (
    ExchangeAuthorizationCode,
    InvalidAuthorizationCodeError,
    LogIn,
    OAuthAuthenticationError,
)
from mosemo.identity.application.tokens import TokenService
from mosemo.identity.bootstrap import IdentityConfig, create_kakao_client
from mosemo.identity.domain.accounts import AccountProvider
from mosemo.identity.domain.pkce import create_code_challenge
from mosemo.identity.infrastructure.kakao_client import KAKAO_AUTHORIZE_URL
from mosemo.identity.presentation.auth.router import (
    PKCE_CHALLENGE_COOKIE,
    STATE_COOKIE,
    OAuthCookies,
    create_oauth_router,
    create_token_router,
)
from mosemo.identity.presentation.auth.schemas import KakaoCallback, TokenRequest
from mosemo.shared.errors import ApiException

CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)
OAUTH_CLIENTS = Mock()
TOKENS = create_autospec(TokenService, instance=True)


def make_service():
    return SimpleNamespace(
        log_in=create_autospec(LogIn, instance=True),
        exchange=create_autospec(ExchangeAuthorizationCode, instance=True),
    )


def endpoints(service, config: IdentityConfig):
    routers = [
        create_oauth_router(
            log_in=service.log_in,
            get_login_client=Mock(),
            get_oauth_clients=Mock(),
            macos_callback_uri=config.macos_callback_uri,
            secure_cookies=config.secure_cookies,
        ),
        create_token_router(
            exchange_authorization_code=service.exchange,
            token_service=TOKENS,
            access_token_ttl_seconds=config.access_token_ttl_seconds,
        ),
    ]
    return {
        route.name: route.endpoint
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute)
    }


def login(config, kakao_client, *, code_challenge, code_challenge_method):
    return endpoints(make_service(), config)["login"](
        kakao_client,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
    )


def callback(*, service, config, kakao_callback, state_cookie, pkce_challenge_cookie):
    return endpoints(service, config)["callback"](
        sentinel.db_session,
        oauth_clients=OAUTH_CLIENTS,
        kakao_callback=kakao_callback,
        cookies=OAuthCookies(state_cookie, pkce_challenge_cookie),
    )


def exchange_token(request, service, *, config, response):
    return endpoints(service, config)["exchange_token"](
        request,
        sentinel.db_session,
        response=response,
    )


def make_kakao_client(config: IdentityConfig):
    return create_kakao_client(config, http_client=cast(httpx2.AsyncClient, object()))


def response_cookies(response) -> SimpleCookie:
    cookies = SimpleCookie()
    for header in response.headers.getlist("set-cookie"):
        cookies.load(header)
    return cookies


def test_login_redirects_to_kakao_and_sets_oauth_cookies(
    identity_config: IdentityConfig,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "mosemo.identity.presentation.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )

    response = login(
        identity_config,
        make_kakao_client(identity_config),
        code_challenge=CODE_CHALLENGE,
        code_challenge_method="S256",
    )

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
    identity_config: IdentityConfig,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "mosemo.identity.presentation.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )
    production_config = dataclasses.replace(identity_config, secure_cookies=True)

    response = login(
        production_config,
        make_kakao_client(production_config),
        code_challenge=CODE_CHALLENGE,
        code_challenge_method="S256",
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
    identity_config: IdentityConfig,
    state: str | None,
    state_cookie: str | None,
    pkce_challenge_cookie: str | None,
) -> None:
    service = make_service()

    response = asyncio.run(
        callback(
            service=service,
            config=identity_config,
            kakao_callback=KakaoCallback(code="authorization-code", state=state),
            state_cookie=state_cookie,
            pkce_challenge_cookie=pkce_challenge_cookie,
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
    service.log_in.execute.assert_not_awaited()


def test_callback_redirects_authorization_code_to_macos_app(
    identity_config: IdentityConfig,
) -> None:
    service = make_service()
    service.log_in.execute.return_value = "one-time-code"

    response = asyncio.run(
        callback(
            service=service,
            config=identity_config,
            kakao_callback=KakaoCallback(
                code="authorization-code", state="valid-state"
            ),
            state_cookie="valid-state",
            pkce_challenge_cookie=CODE_CHALLENGE,
        )
    )

    location = urlsplit(response.headers["location"])
    assert response.status_code == 302
    assert location.scheme == "com.example.mosemo"
    assert location.path == "/auth/callback"
    assert parse_qs(location.query) == {"code": ["one-time-code"]}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    service.log_in.execute.assert_awaited_once_with(
        sentinel.db_session,
        oauth_clients=OAUTH_CLIENTS,
        provider=AccountProvider.KAKAO,
        code="authorization-code",
        code_challenge=CODE_CHALLENGE,
    )
    cookies = response_cookies(response)
    assert cookies[STATE_COOKIE]["max-age"] == "0"
    assert cookies[PKCE_CHALLENGE_COOKIE]["max-age"] == "0"


def test_callback_redirects_authentication_failure_to_macos_app(
    identity_config: IdentityConfig,
) -> None:
    service = make_service()
    service.log_in.execute.side_effect = OAuthAuthenticationError()

    response = asyncio.run(
        callback(
            service=service,
            config=identity_config,
            kakao_callback=KakaoCallback(
                code="authorization-code", state="valid-state"
            ),
            state_cookie="valid-state",
            pkce_challenge_cookie=CODE_CHALLENGE,
        )
    )

    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "error": ["authentication_failed"]
    }
    service.log_in.execute.assert_awaited_once_with(
        sentinel.db_session,
        oauth_clients=OAUTH_CLIENTS,
        provider=AccountProvider.KAKAO,
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
    identity_config: IdentityConfig,
    provider_error: str,
    public_error: str,
) -> None:
    service = make_service()

    response = asyncio.run(
        callback(
            service=service,
            config=identity_config,
            kakao_callback=KakaoCallback(
                state="valid-state",
                error=provider_error,
                error_description="sensitive provider description",
            ),
            state_cookie="valid-state",
            pkce_challenge_cookie=CODE_CHALLENGE,
        )
    )

    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "error": [public_error]
    }
    service.log_in.execute.assert_not_awaited()


def test_exchange_token_returns_bearer_response(
    identity_config: IdentityConfig,
) -> None:
    service = make_service()
    service.exchange.execute.return_value = "access-token"

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
            config=identity_config,
            response=Response(),
        )
    )

    assert result.model_dump() == {
        "accessToken": "access-token",
        "tokenType": "Bearer",
        "expiresIn": 86_400,
    }
    service.exchange.execute.assert_awaited_once_with(
        sentinel.db_session,
        authorization_code="one-time-code",
        code_verifier=CODE_VERIFIER,
        token_service=TOKENS,
    )


def test_exchange_token_returns_fixed_error(identity_config: IdentityConfig) -> None:
    service = make_service()
    service.exchange.execute.side_effect = InvalidAuthorizationCodeError()

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
                config=identity_config,
                response=Response(),
            )
        )

    assert exc_info.value.spec.status == "AUTH_INVALID_AUTHORIZATION_CODE"
    assert exc_info.value.spec.code == 400
    assert exc_info.value.spec.message == "Invalid or expired authorization code"
    assert isinstance(exc_info.value.__cause__, InvalidAuthorizationCodeError)
    service.exchange.execute.assert_awaited_once_with(
        sentinel.db_session,
        authorization_code="invalid-code",
        code_verifier=CODE_VERIFIER,
        token_service=TOKENS,
    )

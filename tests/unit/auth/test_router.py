import asyncio
import json
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import Response

from mosemo.accounts.models import Account, AccountProvider
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.router import (
    KAKAO_AUTHORIZE_URL,
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
    KakaoAuthenticationError,
)
from mosemo.config import Config
from mosemo.exceptions import ApiException

CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)


class StubAuthService(AuthService):
    def __init__(
        self,
        *,
        result: Account | None = None,
        authentication_error: KakaoAuthenticationError | None = None,
        exchange_error: InvalidAuthorizationCodeError | None = None,
    ) -> None:
        self.result = result
        self.authentication_error = authentication_error
        self.exchange_error = exchange_error
        self.kakao_codes: list[str] = []
        self.code_challenges: list[str] = []
        self.exchange_requests: list[tuple[str, str]] = []

    async def authenticate_kakao(self, *, code: str) -> Account:
        self.kakao_codes.append(code)
        if self.authentication_error is not None:
            raise self.authentication_error
        if self.result is None:
            raise AssertionError("An account result was not configured")
        return self.result

    async def create_authorization_code(
        self,
        *,
        account: Account,
        code_challenge: str,
    ) -> str:
        assert account is self.result
        self.code_challenges.append(code_challenge)
        return "one-time-code"

    async def exchange_authorization_code(
        self,
        *,
        authorization_code: str,
        code_verifier: str,
    ) -> str:
        self.exchange_requests.append((authorization_code, code_verifier))
        if self.exchange_error is not None:
            raise self.exchange_error
        return "access-token"


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

    response = login(config, CODE_CHALLENGE, "S256")

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

    response = login(production_config, CODE_CHALLENGE, "S256")

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
    service = StubAuthService()

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
    assert service.kakao_codes == []


def test_callback_redirects_authorization_code_to_macos_app(
    config: Config,
) -> None:
    account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    service = StubAuthService(result=account)

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
    assert service.kakao_codes == ["authorization-code"]
    assert service.code_challenges == [CODE_CHALLENGE]
    cookies = response_cookies(response)
    assert cookies[STATE_COOKIE]["max-age"] == "0"
    assert cookies[PKCE_CHALLENGE_COOKIE]["max-age"] == "0"


def test_callback_redirects_authentication_failure_to_macos_app(
    config: Config,
) -> None:
    service = StubAuthService(authentication_error=KakaoAuthenticationError())

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
    service = StubAuthService()

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
    assert service.kakao_codes == []


def test_exchange_token_returns_bearer_response(config: Config) -> None:
    service = StubAuthService()

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


def test_exchange_token_returns_fixed_error(config: Config) -> None:
    service = StubAuthService(exchange_error=InvalidAuthorizationCodeError())

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

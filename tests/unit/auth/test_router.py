import asyncio
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from mosemo.accounts.models import Account, AccountProvider
from mosemo.auth.router import KAKAO_AUTHORIZE_URL, STATE_COOKIE, callback, login
from mosemo.auth.service import AuthService, KakaoAuthenticationError
from mosemo.config import Config


class StubAuthService(AuthService):
    def __init__(
        self,
        *,
        result: Account | None = None,
        error: KakaoAuthenticationError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.codes: list[str] = []

    async def authenticate_kakao(self, *, code: str) -> Account:
        self.codes.append(code)
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("An account result was not configured")
        return self.result


def test_login_redirects_to_kakao_and_sets_state_cookie(
    config: Config,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "mosemo.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )

    response = login(config)

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
    assert response.status_code == 302

    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    state_cookie = cookies[STATE_COOKIE]
    assert state_cookie.value == "fixed-state"
    assert state_cookie["max-age"] == "600"
    assert state_cookie["httponly"] is True
    assert state_cookie["samesite"] == "lax"
    assert state_cookie["path"] == "/api/v1/auth/kakao"
    assert state_cookie["secure"] == ""


def test_login_uses_secure_cookie_in_production(
    config: Config,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "mosemo.auth.router.secrets.token_urlsafe",
        lambda length: "fixed-state",
    )
    production_config = config.model_copy(update={"app_env": "prod"})

    response = login(production_config)

    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    assert cookies[STATE_COOKIE]["secure"] is True


@pytest.mark.parametrize(
    ("code", "state", "state_cookie"),
    [
        (None, "state", "state"),
        ("code", None, "state"),
        ("code", "state", None),
        ("code", "state", "different-state"),
    ],
)
def test_callback_rejects_missing_or_mismatched_state(
    code: str | None,
    state: str | None,
    state_cookie: str | None,
) -> None:
    service = StubAuthService()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            callback(
                service=service,
                code=code,
                state=state,
                state_cookie=state_cookie,
                error=None,
                error_description=None,
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid Kakao OAuth state"
    assert service.codes == []


def test_callback_authenticates_valid_code() -> None:
    account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    service = StubAuthService(result=account)

    result = asyncio.run(
        callback(
            service=service,
            code="authorization-code",
            state="valid-state",
            state_cookie="valid-state",
            error=None,
            error_description=None,
        )
    )

    assert result is account
    assert service.codes == ["authorization-code"]


def test_callback_converts_authentication_error_to_bad_gateway() -> None:
    service = StubAuthService(error=KakaoAuthenticationError())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            callback(
                service=service,
                code="authorization-code",
                state="valid-state",
                state_cookie="valid-state",
                error=None,
                error_description=None,
            )
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Kakao authentication failed"


def test_callback_returns_provider_error_without_authenticating() -> None:
    service = StubAuthService()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            callback(
                service=service,
                code=None,
                state=None,
                state_cookie=None,
                error="access_denied",
                error_description="user cancelled",
            )
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "kakao login failed: access_denied"
    assert service.codes == []

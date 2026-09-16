import asyncio
from typing import cast
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from mosemo.auth.kakao_client import (
    KAKAO_AUTHORIZE_URL,
    KAKAO_TOKEN_URL,
    KAKAO_USER_INFO_URL,
    KakaoClient,
    KakaoClientError,
)
from mosemo.config import Config


def test_get_user_id_exchanges_code_and_fetches_user(config: Config) -> None:
    requests: list[httpx2.Request] = []

    def handle_request(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url == httpx2.URL(KAKAO_TOKEN_URL):
            return httpx2.Response(200, json={"access_token": "provider-token"})
        if request.url == httpx2.URL(KAKAO_USER_INFO_URL):
            return httpx2.Response(200, json={"id": 123456789})
        raise AssertionError(f"Unexpected request: {request.url}")

    async def run() -> str:
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handle_request)
        ) as http_client:
            client = KakaoClient(http_client=http_client, config=config.kakao)
            return await client.get_user_id(code="authorization-code")

    provider_subject = asyncio.run(run())

    assert provider_subject == "123456789"
    assert len(requests) == 2
    assert parse_qs(requests[0].content.decode()) == {
        "grant_type": ["authorization_code"],
        "client_id": ["test-rest-api-key"],
        "redirect_uri": ["http://localhost:8000/api/v1/auth/kakao/callback"],
        "code": ["authorization-code"],
        "client_secret": ["test-client-secret"],
    }
    assert requests[1].headers["authorization"] == "Bearer provider-token"


@pytest.mark.parametrize(
    ("token_status", "token_payload"),
    [
        (500, {"error": "provider unavailable"}),
        (200, {}),
    ],
)
def test_get_user_id_converts_token_failure(
    config: Config,
    token_status: int,
    token_payload: dict,
) -> None:
    def handle_request(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(token_status, json=token_payload)

    async def run() -> None:
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handle_request)
        ) as http_client:
            client = KakaoClient(http_client=http_client, config=config.kakao)
            with pytest.raises(KakaoClientError):
                await client.get_user_id(code="authorization-code")

    asyncio.run(run())


def test_get_user_id_converts_user_endpoint_failure(config: Config) -> None:
    def handle_request(request: httpx2.Request) -> httpx2.Response:
        if request.url == httpx2.URL(KAKAO_TOKEN_URL):
            return httpx2.Response(200, json={"access_token": "provider-token"})
        return httpx2.Response(500, json={"error": "provider unavailable"})

    async def run() -> None:
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(handle_request)
        ) as http_client:
            client = KakaoClient(http_client=http_client, config=config.kakao)
            with pytest.raises(KakaoClientError):
                await client.get_user_id(code="authorization-code")

    asyncio.run(run())


def test_create_authorization_url_uses_config_and_state(config: Config) -> None:
    client = KakaoClient(
        http_client=cast(httpx2.AsyncClient, object()),
        config=config.kakao,
    )

    location = urlsplit(client.create_authorization_url(state="fixed-state"))

    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        KAKAO_AUTHORIZE_URL
    )
    assert parse_qs(location.query) == {
        "client_id": ["test-rest-api-key"],
        "redirect_uri": ["http://localhost:8000/api/v1/auth/kakao/callback"],
        "response_type": ["code"],
        "state": ["fixed-state"],
    }

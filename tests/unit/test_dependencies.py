from enum import StrEnum
from typing import cast
from unittest.mock import Mock

import httpx2
import pytest

from mosemo.identity.bootstrap import get_oauth_client
from mosemo.identity.domain.accounts import AccountProvider
from mosemo.identity.infrastructure.kakao_client import KakaoClient
from mosemo.platform.config import Config


class OtherProvider(StrEnum):
    KAKAO = "KAKAO"


def test_get_oauth_client_resolves_kakao(config: Config) -> None:
    client = get_oauth_client(
        AccountProvider.KAKAO,
        config,
        http_client=cast(httpx2.AsyncClient, object()),
    )

    assert isinstance(client, KakaoClient)


@pytest.mark.parametrize("provider", ["KAKAO", OtherProvider.KAKAO])
def test_get_oauth_client_asserts_account_provider_before_creating_client(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    provider: object,
) -> None:
    kakao_client = Mock()
    monkeypatch.setattr("mosemo.identity.bootstrap.KakaoClient", kakao_client)

    with pytest.raises(AssertionError):
        get_oauth_client(
            cast(AccountProvider, provider),
            config,
            http_client=cast(httpx2.AsyncClient, object()),
        )

    kakao_client.assert_not_called()

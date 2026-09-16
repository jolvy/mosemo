from enum import StrEnum
from typing import cast
from unittest.mock import Mock

import httpx2
import pytest

from mosemo.accounts.models import AccountProvider
from mosemo.auth.kakao_client import KakaoClient
from mosemo.config import Config
from mosemo.dependencies import get_oauth_client


class OtherProvider(StrEnum):
    KAKAO = "KAKAO"


def test_get_oauth_client_resolves_kakao(config: Config) -> None:
    client = get_oauth_client(
        AccountProvider.KAKAO,
        config,
        cast(httpx2.AsyncClient, object()),
    )

    assert isinstance(client, KakaoClient)


@pytest.mark.parametrize("provider", ["KAKAO", OtherProvider.KAKAO])
def test_get_oauth_client_asserts_account_provider_before_creating_client(
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    provider: object,
) -> None:
    kakao_client = Mock()
    monkeypatch.setattr("mosemo.dependencies.KakaoClient", kakao_client)

    with pytest.raises(AssertionError):
        get_oauth_client(
            cast(AccountProvider, provider),
            config,
            cast(httpx2.AsyncClient, object()),
        )

    kakao_client.assert_not_called()

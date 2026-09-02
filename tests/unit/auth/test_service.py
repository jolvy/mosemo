import asyncio
from unittest.mock import create_autospec

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.auth.kakao_client import KakaoClient, KakaoClientError
from mosemo.auth.service import AuthService, KakaoAuthenticationError


def make_service():
    kakao_client = create_autospec(KakaoClient, instance=True)
    session = create_autospec(AsyncSession, instance=True)
    account_repository = create_autospec(AccountRepository, instance=True)
    service = AuthService(
        kakao_client=kakao_client,
        session=session,
        account_repository=account_repository,
    )
    return service, kakao_client, session, account_repository


def test_authenticate_kakao_returns_existing_account_without_writing() -> None:
    service, kakao_client, session, account_repository = make_service()
    existing_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    kakao_client.get_user_id.return_value = "123456789"
    account_repository.find.return_value = existing_account

    result = asyncio.run(service.authenticate_kakao(code="authorization-code"))

    assert result is existing_account
    kakao_client.get_user_id.assert_awaited_once_with(code="authorization-code")
    account_repository.find.assert_awaited_once_with(
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    account_repository.save.assert_not_called()
    session.commit.assert_not_awaited()
    session.refresh.assert_not_awaited()


def test_authenticate_kakao_saves_new_account() -> None:
    service, kakao_client, session, account_repository = make_service()
    new_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    kakao_client.get_user_id.return_value = "123456789"
    account_repository.find.return_value = None
    account_repository.save.return_value = new_account

    result = asyncio.run(service.authenticate_kakao(code="authorization-code"))

    assert result is new_account
    account_repository.save.assert_called_once_with(
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    session.commit.assert_awaited_once_with()
    session.refresh.assert_awaited_once_with(new_account)


def test_authenticate_kakao_converts_client_error() -> None:
    service, kakao_client, session, account_repository = make_service()
    kakao_client.get_user_id.side_effect = KakaoClientError()

    with pytest.raises(KakaoAuthenticationError):
        asyncio.run(service.authenticate_kakao(code="authorization-code"))

    account_repository.find.assert_not_awaited()
    account_repository.save.assert_not_called()
    session.commit.assert_not_awaited()

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.auth.kakao_client import KakaoClient, KakaoClientError
from mosemo.auth.models import NativeAuthCode
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.repository import NativeAuthCodeRepository
from mosemo.auth.service import (
    AuthService,
    InvalidAuthorizationCodeError,
    KakaoAuthenticationError,
)
from mosemo.auth.tokens import TokenService
from mosemo.config import Config

CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)


def make_service(config: Config):
    kakao_client = create_autospec(KakaoClient, instance=True)
    session = create_autospec(AsyncSession, instance=True)
    account_repository = create_autospec(AccountRepository, instance=True)
    auth_code_repository = create_autospec(
        NativeAuthCodeRepository,
        instance=True,
    )
    token_service = create_autospec(TokenService, instance=True)
    service = AuthService(
        kakao_client=kakao_client,
        session=session,
        account_repository=account_repository,
        native_auth_code_repository=auth_code_repository,
        token_service=token_service,
        config=config.auth,
    )
    return (
        service,
        kakao_client,
        session,
        account_repository,
        auth_code_repository,
        token_service,
    )


def test_authenticate_kakao_updates_existing_account(config: Config) -> None:
    service, kakao_client, session, account_repository, _, _ = make_service(config)
    existing_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    kakao_client.get_user_id.return_value = "123456789"
    account_repository.find.return_value = existing_account

    before = datetime.now(UTC)
    result = asyncio.run(service.authenticate_kakao(code="authorization-code"))

    assert result is existing_account
    assert existing_account.last_authenticated_at >= before
    account_repository.save.assert_not_called()
    session.commit.assert_awaited_once_with()
    session.refresh.assert_awaited_once_with(existing_account)


def test_authenticate_kakao_saves_new_account(config: Config) -> None:
    service, kakao_client, session, account_repository, _, _ = make_service(config)
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


def test_authenticate_kakao_converts_client_error(config: Config) -> None:
    service, kakao_client, session, account_repository, _, _ = make_service(config)
    kakao_client.get_user_id.side_effect = KakaoClientError()

    with pytest.raises(KakaoAuthenticationError):
        asyncio.run(service.authenticate_kakao(code="authorization-code"))

    account_repository.find.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_create_authorization_code_stores_only_digest(
    config: Config,
    monkeypatch,
) -> None:
    service, _, session, _, auth_code_repository, _ = make_service(config)
    account = Account(
        account_id=uuid4(),
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    monkeypatch.setattr(
        "mosemo.auth.service.secrets.token_urlsafe",
        lambda length: "one-time-authorization-code",
    )

    result = asyncio.run(
        service.create_authorization_code(
            account=account,
            code_challenge=CODE_CHALLENGE,
        )
    )

    assert result == "one-time-authorization-code"
    auth_code_repository.delete_expired.assert_awaited_once()
    save_call = auth_code_repository.save.call_args
    assert (
        save_call.kwargs["code_digest"] == hashlib.sha256(result.encode()).hexdigest()
    )
    assert save_call.kwargs["code_digest"] != result
    assert save_call.kwargs["account_id"] == account.account_id
    assert save_call.kwargs["code_challenge"] == CODE_CHALLENGE
    session.commit.assert_awaited_once_with()


def test_exchange_authorization_code_consumes_code_and_issues_token(
    config: Config,
) -> None:
    service, _, session, _, auth_code_repository, token_service = make_service(config)
    account_id = uuid4()
    stored_code = NativeAuthCode(
        code_digest="digest",
        account_id=account_id,
        code_challenge=CODE_CHALLENGE,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    auth_code_repository.find_for_update.return_value = stored_code
    token_service.issue_access_token.return_value = "access-token"

    result = asyncio.run(
        service.exchange_authorization_code(
            authorization_code="one-time-code",
            code_verifier=CODE_VERIFIER,
        )
    )

    assert result == "access-token"
    auth_code_repository.find_for_update.assert_awaited_once_with(
        hashlib.sha256(b"one-time-code").hexdigest()
    )
    auth_code_repository.delete.assert_awaited_once_with(stored_code)
    session.commit.assert_awaited_once_with()
    token_service.issue_access_token.assert_called_once_with(account_id)


def test_exchange_authorization_code_rejects_wrong_verifier(config: Config) -> None:
    service, _, session, _, auth_code_repository, token_service = make_service(config)
    stored_code = NativeAuthCode(
        code_digest="digest",
        account_id=uuid4(),
        code_challenge=CODE_CHALLENGE,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    auth_code_repository.find_for_update.return_value = stored_code

    with pytest.raises(InvalidAuthorizationCodeError):
        asyncio.run(
            service.exchange_authorization_code(
                authorization_code="one-time-code",
                code_verifier="B" * 43,
            )
        )

    session.rollback.assert_awaited_once_with()
    auth_code_repository.delete.assert_not_awaited()
    token_service.issue_access_token.assert_not_called()


def test_exchange_authorization_code_deletes_expired_code(config: Config) -> None:
    service, _, session, _, auth_code_repository, token_service = make_service(config)
    stored_code = NativeAuthCode(
        code_digest="digest",
        account_id=uuid4(),
        code_challenge=CODE_CHALLENGE,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    auth_code_repository.find_for_update.return_value = stored_code

    with pytest.raises(InvalidAuthorizationCodeError):
        asyncio.run(
            service.exchange_authorization_code(
                authorization_code="one-time-code",
                code_verifier=CODE_VERIFIER,
            )
        )

    auth_code_repository.delete.assert_awaited_once_with(stored_code)
    session.commit.assert_awaited_once_with()
    token_service.issue_access_token.assert_not_called()


def test_exchange_authorization_code_rejects_unknown_code(config: Config) -> None:
    service, _, session, _, auth_code_repository, token_service = make_service(config)
    auth_code_repository.find_for_update.return_value = None

    with pytest.raises(InvalidAuthorizationCodeError):
        asyncio.run(
            service.exchange_authorization_code(
                authorization_code="unknown-code",
                code_verifier=CODE_VERIFIER,
            )
        )

    session.rollback.assert_awaited_once_with()
    token_service.issue_access_token.assert_not_called()

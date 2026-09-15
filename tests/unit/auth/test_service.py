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


def test_complete_kakao_login_updates_existing_account_and_stores_code(
    config: Config,
    monkeypatch,
) -> None:
    service, kakao_client, session, account_repository, auth_code_repository, _ = (
        make_service(config)
    )
    existing_account = Account(
        account_id=uuid4(),
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    kakao_client.get_user_id.return_value = "123456789"
    account_repository.find.return_value = existing_account
    monkeypatch.setattr(
        "mosemo.auth.service.secrets.token_urlsafe",
        lambda length: "one-time-authorization-code",
    )

    before = datetime.now(UTC)
    result = asyncio.run(
        service.complete_kakao_login(
            code="authorization-code",
            code_challenge=CODE_CHALLENGE,
        )
    )

    assert result == "one-time-authorization-code"
    assert existing_account.last_authenticated_at >= before
    account_repository.save.assert_not_called()
    session.begin.assert_called_once_with()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.refresh.assert_not_awaited()
    auth_code_repository.delete_expired.assert_awaited_once()
    save_call = auth_code_repository.save.call_args
    assert (
        save_call.kwargs["code_digest"] == hashlib.sha256(result.encode()).hexdigest()
    )
    assert save_call.kwargs["code_digest"] != result
    assert save_call.kwargs["account_id"] == existing_account.account_id
    assert save_call.kwargs["code_challenge"] == CODE_CHALLENGE


def test_complete_kakao_login_flushes_new_account_before_storing_code(
    config: Config,
) -> None:
    service, kakao_client, session, account_repository, auth_code_repository, _ = (
        make_service(config)
    )
    new_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    account_id = uuid4()

    async def assign_account_id() -> None:
        new_account.account_id = account_id

    kakao_client.get_user_id.return_value = "123456789"
    account_repository.find.return_value = None
    account_repository.save.return_value = new_account
    session.flush.side_effect = assign_account_id

    result = asyncio.run(
        service.complete_kakao_login(
            code="authorization-code",
            code_challenge=CODE_CHALLENGE,
        )
    )

    assert result
    account_repository.save.assert_called_once_with(
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    session.begin.assert_called_once_with()
    session.flush.assert_awaited_once_with()
    assert auth_code_repository.save.call_args.kwargs["account_id"] == account_id


def test_complete_kakao_login_converts_client_error(config: Config) -> None:
    service, kakao_client, session, account_repository, _, _ = make_service(config)
    kakao_client.get_user_id.side_effect = KakaoClientError()

    with pytest.raises(KakaoAuthenticationError):
        asyncio.run(
            service.complete_kakao_login(
                code="authorization-code",
                code_challenge=CODE_CHALLENGE,
            )
        )

    account_repository.find.assert_not_awaited()
    session.begin.assert_not_called()
    session.commit.assert_not_awaited()


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
    session.begin.assert_called_once_with()
    auth_code_repository.find_for_update.assert_awaited_once_with(
        hashlib.sha256(b"one-time-code").hexdigest()
    )
    auth_code_repository.delete.assert_awaited_once_with(stored_code)
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    token_service.issue_access_token.assert_called_once_with(account_id)


def test_exchange_authorization_code_preserves_code_when_token_issuance_fails(
    config: Config,
) -> None:
    service, _, session, _, auth_code_repository, token_service = make_service(config)
    stored_code = NativeAuthCode(
        code_digest="digest",
        account_id=uuid4(),
        code_challenge=CODE_CHALLENGE,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    auth_code_repository.find_for_update.return_value = stored_code
    token_service.issue_access_token.side_effect = RuntimeError("token issuance failed")

    with pytest.raises(RuntimeError, match="token issuance failed"):
        asyncio.run(
            service.exchange_authorization_code(
                authorization_code="one-time-code",
                code_verifier=CODE_VERIFIER,
            )
        )

    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    auth_code_repository.delete.assert_not_awaited()
    session.commit.assert_not_awaited()


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

    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    auth_code_repository.delete.assert_not_awaited()
    session.commit.assert_not_awaited()
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

    session.begin.assert_called_once_with()
    auth_code_repository.delete.assert_awaited_once_with(stored_code)
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
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

    session.begin.assert_called_once_with()
    session.rollback.assert_not_awaited()
    session.commit.assert_not_awaited()
    token_service.issue_access_token.assert_not_called()

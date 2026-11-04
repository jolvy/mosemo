import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, create_autospec, sentinel
from uuid import uuid4

import pytest

from mosemo.identity.application.ports import OAuthClient
from mosemo.identity.application.sign_in import (
    ExchangeAuthorizationCode,
    InvalidAuthorizationCodeError,
    LogIn,
    OAuthAuthenticationError,
)
from mosemo.identity.application.tokens import TokenService
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.authorization_codes import NativeAuthCode
from mosemo.identity.domain.events import AccountCreated
from mosemo.identity.domain.oauth import OAuthClientError
from mosemo.identity.domain.pkce import create_code_challenge
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.identity.infrastructure.authorization_codes import (
    NativeAuthCodeRepository,
)
from mosemo.infra.config import Config
from mosemo.shared.application.events import EventDispatcher
from tests.clock_support import FakeClock
from tests.use_case_support import TransactionRecorder, record_transactions

NOW = datetime(2026, 9, 1, tzinfo=UTC)
CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)


@pytest.fixture
def transactions(monkeypatch: pytest.MonkeyPatch) -> TransactionRecorder:
    return record_transactions(
        monkeypatch, "mosemo.identity.application.sign_in", "unit_of_work"
    )


def make_login(config: Config, clock: FakeClock):
    oauth_client = create_autospec(OAuthClient, instance=True)
    account_repository = create_autospec(AccountRepository, instance=True)
    auth_code_repository = create_autospec(NativeAuthCodeRepository, instance=True)
    get_oauth_client = Mock(return_value=oauth_client)
    use_case = LogIn(
        accounts=account_repository,
        auth_codes=auth_code_repository,
        dispatcher=EventDispatcher(),
        clock=clock,
        authorization_code_ttl=timedelta(
            seconds=config.auth.authorization_code_ttl_seconds
        ),
    )
    return use_case, get_oauth_client, account_repository, auth_code_repository


def log_in(use_case: LogIn, get_oauth_client: Mock) -> str:
    return asyncio.run(
        use_case.execute(
            sentinel.db_session,
            oauth_clients=get_oauth_client,
            provider=AccountProvider.KAKAO,
            code="authorization-code",
            code_challenge=CODE_CHALLENGE,
        )
    )


def make_exchange(clock: FakeClock):
    auth_code_repository = create_autospec(NativeAuthCodeRepository, instance=True)
    token_service = create_autospec(TokenService, instance=True)
    use_case = ExchangeAuthorizationCode(
        auth_codes=auth_code_repository, dispatcher=EventDispatcher(), clock=clock
    )
    return use_case, auth_code_repository, token_service


def exchange(use_case, token_service, *, code: str, verifier: str) -> str:
    return asyncio.run(
        use_case.execute(
            sentinel.db_session,
            authorization_code=code,
            code_verifier=verifier,
            token_service=token_service,
        )
    )


def test_login_updates_existing_account_and_stores_code(
    config: Config,
    monkeypatch,
    transactions: TransactionRecorder,
) -> None:
    fake_clock = FakeClock(NOW)
    use_case, get_oauth_client, account_repository, auth_code_repository = make_login(
        config, fake_clock
    )
    oauth_client = get_oauth_client.return_value
    existing_account = Account(
        id=uuid4(),
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    oauth_client.get_user_id.return_value = "123456789"
    account_repository.get_or_create.return_value = (existing_account, False)
    monkeypatch.setattr(
        "mosemo.identity.domain.authorization_codes.secrets.token_urlsafe",
        lambda length: "one-time-authorization-code",
    )

    before = fake_clock.now
    result = log_in(use_case, get_oauth_client)

    assert result == "one-time-authorization-code"
    get_oauth_client.assert_called_once_with(AccountProvider.KAKAO)
    account_repository.get_or_create.assert_awaited_once()
    _, candidate = account_repository.get_or_create.await_args.args
    assert (candidate.provider, candidate.provider_subject) == (
        AccountProvider.KAKAO,
        "123456789",
    )
    assert existing_account.last_authenticated_at >= before
    assert existing_account.events == []
    assert (transactions.entered, transactions.committed) == (1, 1)
    auth_code_repository.delete_expired.assert_awaited_once()
    (_, stored_code) = auth_code_repository.add.call_args.args
    assert stored_code.code_digest == hashlib.sha256(result.encode()).hexdigest()
    assert stored_code.code_digest != result
    assert stored_code.account_id == existing_account.id
    assert stored_code.code_challenge == CODE_CHALLENGE


def test_login_records_account_created_and_stores_code_for_new_account(
    config: Config,
    transactions: TransactionRecorder,
) -> None:
    fake_clock = FakeClock(NOW)
    use_case, get_oauth_client, account_repository, auth_code_repository = make_login(
        config, fake_clock
    )
    oauth_client = get_oauth_client.return_value
    account_id = uuid4()
    oauth_client.get_user_id.return_value = "123456789"
    created = Account(
        id=account_id,
        provider=AccountProvider.KAKAO,
        provider_subject="123456789",
    )
    account_repository.get_or_create.return_value = (created, True)

    result = log_in(use_case, get_oauth_client)

    assert result
    account_repository.get_or_create.assert_awaited_once()
    # Default labels come from the AccountCreated handler, not from sign-in.
    assert created.events == [AccountCreated(account_id=account_id)]
    assert (transactions.entered, transactions.committed) == (1, 1)
    (_, stored_code) = auth_code_repository.add.call_args.args
    assert stored_code.account_id == account_id


def test_login_fails_when_account_conflict_cannot_be_resolved(
    config: Config,
    transactions: TransactionRecorder,
) -> None:
    fake_clock = FakeClock(NOW)
    use_case, get_oauth_client, account_repository, auth_code_repository = make_login(
        config, fake_clock
    )
    get_oauth_client.return_value.get_user_id.return_value = "123456789"
    account_repository.get_or_create.side_effect = RuntimeError(
        "account conflict could not be resolved"
    )

    with pytest.raises(RuntimeError, match="account conflict could not be resolved"):
        log_in(use_case, get_oauth_client)

    auth_code_repository.add.assert_not_called()
    assert transactions.committed == 0


def test_login_converts_client_error(
    config: Config, transactions: TransactionRecorder
) -> None:
    fake_clock = FakeClock(NOW)
    use_case, get_oauth_client, account_repository, _ = make_login(config, fake_clock)
    get_oauth_client.return_value.get_user_id.side_effect = OAuthClientError()

    with pytest.raises(OAuthAuthenticationError):
        log_in(use_case, get_oauth_client)

    account_repository.get_or_create.assert_not_awaited()
    assert transactions.entered == 0


def test_exchange_authorization_code_consumes_code_and_issues_token(
    transactions: TransactionRecorder,
) -> None:
    fake_clock = FakeClock(NOW)
    use_case, auth_code_repository, token_service = make_exchange(fake_clock)
    account_id = uuid4()
    stored_code = NativeAuthCode(
        code_digest="digest",
        account_id=account_id,
        code_challenge=CODE_CHALLENGE,
        expires_at=fake_clock.now + timedelta(minutes=1),
    )
    auth_code_repository.find_for_update.return_value = stored_code
    token_service.issue_access_token.return_value = "access-token"

    result = exchange(
        use_case, token_service, code="one-time-code", verifier=CODE_VERIFIER
    )

    assert result == "access-token"
    assert (transactions.entered, transactions.committed) == (1, 1)
    auth_code_repository.find_for_update.assert_awaited_once_with(
        sentinel.db_session, hashlib.sha256(b"one-time-code").hexdigest()
    )
    auth_code_repository.delete.assert_awaited_once_with(
        sentinel.db_session, stored_code
    )
    token_service.issue_access_token.assert_called_once_with(
        account_id, now=fake_clock.now
    )


def test_exchange_authorization_code_preserves_code_when_token_issuance_fails(
    transactions: TransactionRecorder,
) -> None:
    fake_clock = FakeClock(NOW)
    use_case, auth_code_repository, token_service = make_exchange(fake_clock)
    stored_code = NativeAuthCode(
        code_digest="digest",
        account_id=uuid4(),
        code_challenge=CODE_CHALLENGE,
        expires_at=fake_clock.now + timedelta(minutes=1),
    )
    auth_code_repository.find_for_update.return_value = stored_code
    token_service.issue_access_token.side_effect = RuntimeError("token issuance failed")

    with pytest.raises(RuntimeError, match="token issuance failed"):
        exchange(use_case, token_service, code="one-time-code", verifier=CODE_VERIFIER)

    assert (transactions.entered, transactions.committed) == (1, 0)
    auth_code_repository.delete.assert_not_awaited()


def test_exchange_authorization_code_rejects_wrong_verifier(
    transactions: TransactionRecorder,
) -> None:
    fake_clock = FakeClock(NOW)
    use_case, auth_code_repository, token_service = make_exchange(fake_clock)
    stored_code = NativeAuthCode(
        code_digest="digest",
        account_id=uuid4(),
        code_challenge=CODE_CHALLENGE,
        expires_at=fake_clock.now + timedelta(minutes=1),
    )
    auth_code_repository.find_for_update.return_value = stored_code

    with pytest.raises(InvalidAuthorizationCodeError):
        exchange(use_case, token_service, code="one-time-code", verifier="B" * 43)

    assert (transactions.entered, transactions.committed) == (1, 0)
    auth_code_repository.delete.assert_not_awaited()
    token_service.issue_access_token.assert_not_called()


def test_exchange_authorization_code_deletes_expired_code(
    transactions: TransactionRecorder,
) -> None:
    fake_clock = FakeClock(NOW)
    use_case, auth_code_repository, token_service = make_exchange(fake_clock)
    stored_code = NativeAuthCode(
        code_digest="digest",
        account_id=uuid4(),
        code_challenge=CODE_CHALLENGE,
        expires_at=fake_clock.now - timedelta(seconds=1),
    )
    auth_code_repository.find_for_update.return_value = stored_code

    with pytest.raises(InvalidAuthorizationCodeError):
        exchange(use_case, token_service, code="one-time-code", verifier=CODE_VERIFIER)

    # The deletion commits before the code is rejected.
    assert (transactions.entered, transactions.committed) == (1, 1)
    auth_code_repository.delete.assert_awaited_once_with(
        sentinel.db_session, stored_code
    )
    token_service.issue_access_token.assert_not_called()


def test_exchange_authorization_code_rejects_unknown_code(
    transactions: TransactionRecorder,
) -> None:
    fake_clock = FakeClock(NOW)
    use_case, auth_code_repository, token_service = make_exchange(fake_clock)
    auth_code_repository.find_for_update.return_value = None

    with pytest.raises(InvalidAuthorizationCodeError):
        exchange(use_case, token_service, code="unknown-code", verifier=CODE_VERIFIER)

    assert (transactions.entered, transactions.committed) == (1, 0)
    token_service.issue_access_token.assert_not_called()

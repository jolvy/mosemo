import asyncio
import contextlib
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, create_autospec
from uuid import UUID

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.activity_labels.catalog.models import Label, default_labels
from mosemo.identity.application.ports import OAuthClient
from mosemo.identity.application.sign_in import (
    ExchangeAuthorizationCode,
    InvalidAuthorizationCodeError,
    LogIn,
)
from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.domain.authorization_codes import NativeAuthCode
from mosemo.identity.domain.pkce import create_code_challenge
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.identity.infrastructure.authorization_codes import (
    NativeAuthCodeRepository,
)
from mosemo.identity.infrastructure.kakao_client import KakaoClient
from mosemo.identity.infrastructure.tables import accounts as account_table
from mosemo.identity.presentation.auth.router import get_token_service
from mosemo.infra.clock import utc_now
from mosemo.infra.config import Config
from mosemo.shared.application.clock import Clock
from tests.clock_support import FakeClock
from tests.identity_support import delete_accounts, sign_up_dispatcher

CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)


class SignIn:
    """The sign-in use cases bound to one session, as the API calls them."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        oauth_client: OAuthClient,
        auth_codes: NativeAuthCodeRepository,
        config: Config,
        clock: Clock,
    ) -> None:
        self._session = session
        self._oauth_client = oauth_client
        self._config = config
        # The same temporary default label handler as the API (AccountCreated).
        dispatcher = sign_up_dispatcher()
        self._log_in = LogIn(
            accounts=AccountRepository(),
            auth_codes=auth_codes,
            dispatcher=dispatcher,
            clock=clock,
            authorization_code_ttl=timedelta(
                seconds=config.auth.authorization_code_ttl_seconds
            ),
        )
        self._exchange = ExchangeAuthorizationCode(
            auth_codes=auth_codes, dispatcher=dispatcher, clock=clock
        )

    async def login(
        self, *, provider: AccountProvider, code: str, code_challenge: str
    ) -> str:
        return await self._log_in.execute(
            self._session,
            oauth_clients=lambda _provider: self._oauth_client,
            provider=provider,
            code=code,
            code_challenge=code_challenge,
        )

    async def exchange_authorization_code(
        self, *, authorization_code: str, code_verifier: str
    ) -> str:
        return await self._exchange.execute(
            self._session,
            authorization_code=authorization_code,
            code_verifier=code_verifier,
            token_service=get_token_service(self._config),
        )


async def find_account(session: AsyncSession, provider_subject: str) -> Account | None:
    return await session.scalar(
        select(Account).where(
            account_table.c.provider == AccountProvider.KAKAO,
            account_table.c.provider_subject == provider_subject,
        )
    )


def make_service(
    *,
    session: AsyncSession,
    config: Config,
    clock: Clock,
) -> SignIn:
    return SignIn(
        session=session,
        oauth_client=create_autospec(KakaoClient, instance=True),
        auth_codes=NativeAuthCodeRepository(),
        config=config,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_kakao_login_rolls_back_account_when_code_storage_fails(
    integration_session: AsyncSession,
    config: Config,
) -> None:
    provider_subject = f"integration-{secrets.token_hex(8)}"
    kakao_client = create_autospec(KakaoClient, instance=True)
    kakao_client.get_user_id.return_value = provider_subject
    auth_code_repository = create_autospec(
        NativeAuthCodeRepository,
        instance=True,
    )
    auth_code_repository.add.side_effect = RuntimeError("code storage failed")
    service = SignIn(
        session=integration_session,
        oauth_client=kakao_client,
        auth_codes=auth_code_repository,
        config=config,
        clock=FakeClock(utc_now()),
    )

    with pytest.raises(RuntimeError, match="code storage failed"):
        await service.login(
            provider=AccountProvider.KAKAO,
            code="authorization-code",
            code_challenge=CODE_CHALLENGE,
        )

    assert await find_account(integration_session, provider_subject) is None
    assert (await integration_session.scalars(select(Label))).all() == []


@pytest.mark.asyncio
async def test_kakao_login_creates_defaults_once_and_keeps_label_ids(
    integration_session: AsyncSession,
    config: Config,
) -> None:
    provider_subject = f"integration-{secrets.token_hex(8)}"
    kakao_client = create_autospec(KakaoClient, instance=True)
    kakao_client.get_user_id.return_value = provider_subject
    service = SignIn(
        session=integration_session,
        oauth_client=kakao_client,
        auth_codes=NativeAuthCodeRepository(),
        config=config,
        clock=FakeClock(utc_now()),
    )

    await service.login(
        provider=AccountProvider.KAKAO,
        code="authorization-code",
        code_challenge=CODE_CHALLENGE,
    )
    account = await find_account(integration_session, provider_subject)
    assert account is not None
    first_labels = (
        await integration_session.scalars(
            select(Label)
            .where(Label.account_id == account.id)
            .order_by(Label.display_name)
        )
    ).all()
    assert len(first_labels) == 5
    first_label_ids = [label.label_id for label in first_labels]
    assert sorted(label.display_name for label in first_labels) == sorted(
        ("코딩", "학습", "소통", "쇼핑", "여가")
    )
    await integration_session.commit()

    await service.login(
        provider=AccountProvider.KAKAO,
        code="authorization-code-again",
        code_challenge=CODE_CHALLENGE,
    )
    second_labels = (
        await integration_session.scalars(
            select(Label)
            .where(Label.account_id == account.id)
            .order_by(Label.display_name)
        )
    ).all()
    assert [label.label_id for label in second_labels] == first_label_ids
    assert len(second_labels) == 5


@pytest.mark.asyncio
async def test_kakao_login_rolls_back_existing_account_update_when_code_storage_fails(
    integration_database_url: str,
    config: Config,
    monkeypatch,
) -> None:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    provider_subject = f"integration-{secrets.token_hex(8)}"
    account_id: UUID | None = None
    initial_last_authenticated_at = datetime(2000, 1, 1, tzinfo=UTC)

    try:
        async with session_factory() as session:
            account = Account(
                provider=AccountProvider.KAKAO,
                provider_subject=provider_subject,
            )
            account.last_authenticated_at = initial_last_authenticated_at
            session.add(account)
            await session.commit()
            await session.refresh(account)
            account_id = account.id
            initial_last_authenticated_at = account.last_authenticated_at

        async with session_factory() as session:
            kakao_client = create_autospec(KakaoClient, instance=True)
            kakao_client.get_user_id.return_value = provider_subject
            auth_code_repository = NativeAuthCodeRepository()
            add_auth_code = Mock(
                side_effect=RuntimeError("code storage failed"),
            )
            monkeypatch.setattr(auth_code_repository, "add", add_auth_code)
            service = SignIn(
                session=session,
                oauth_client=kakao_client,
                auth_codes=auth_code_repository,
                config=config,
                clock=FakeClock(utc_now()),
            )

            with pytest.raises(RuntimeError, match="code storage failed"):
                await service.login(
                    provider=AccountProvider.KAKAO,
                    code="authorization-code",
                    code_challenge=CODE_CHALLENGE,
                )

            add_auth_code.assert_called_once()

        async with session_factory() as session:
            stored_account = await find_account(session, provider_subject)
            assert stored_account is not None
            assert stored_account.id == account_id
            assert stored_account.last_authenticated_at == initial_last_authenticated_at
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(account_table).where(
                    account_table.c.provider == AccountProvider.KAKAO,
                    account_table.c.provider_subject == provider_subject,
                )
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_authorization_code_is_hashed_and_consumed_once_concurrently(
    integration_database_url: str,
    config: Config,
) -> None:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    account_id: UUID | None = None

    fake_clock = FakeClock(utc_now())

    try:
        authorization_code = secrets.token_urlsafe(32)
        code_digest = hashlib.sha256(authorization_code.encode()).hexdigest()
        async with session_factory() as session:
            account = Account(
                provider=AccountProvider.KAKAO,
                provider_subject=f"integration-{secrets.token_hex(8)}",
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            account_id = account.id

            session.add(
                NativeAuthCode(
                    code_digest=code_digest,
                    account_id=account_id,
                    code_challenge=CODE_CHALLENGE,
                    expires_at=fake_clock.now + timedelta(minutes=1),
                )
            )
            await session.commit()

        async with session_factory() as session:
            stored_code = await session.get(NativeAuthCode, code_digest)
            assert stored_code is not None
            assert stored_code.code_digest != authorization_code
            assert await session.get(NativeAuthCode, authorization_code) is None

        async def exchange() -> str | None:
            async with session_factory() as session:
                try:
                    return await make_service(
                        session=session,
                        config=config,
                        clock=fake_clock,
                    ).exchange_authorization_code(
                        authorization_code=authorization_code,
                        code_verifier=CODE_VERIFIER,
                    )
                except InvalidAuthorizationCodeError:
                    return None

        results = await asyncio.gather(exchange(), exchange())

        tokens = [result for result in results if result is not None]
        assert len(tokens) == 1
        assert get_token_service(config).decode_access_token(tokens[0]) == account_id
        async with session_factory() as session:
            assert await session.get(NativeAuthCode, code_digest) is None
    finally:
        if account_id is not None:
            async with session_factory() as session:
                await session.execute(delete_accounts(account_id))
                await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_authorization_code_is_deleted(
    integration_database_url: str,
    config: Config,
) -> None:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    account_id: UUID | None = None

    fake_clock = FakeClock(utc_now())

    try:
        authorization_code = secrets.token_urlsafe(32)
        code_digest = hashlib.sha256(authorization_code.encode()).hexdigest()
        async with session_factory() as session:
            account = Account(
                provider=AccountProvider.KAKAO,
                provider_subject=f"integration-{secrets.token_hex(8)}",
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            account_id = account.id

            session.add(
                NativeAuthCode(
                    code_digest=code_digest,
                    account_id=account_id,
                    code_challenge=CODE_CHALLENGE,
                    expires_at=fake_clock.now - timedelta(seconds=1),
                )
            )
            await session.commit()

        async with session_factory() as session:
            with pytest.raises(InvalidAuthorizationCodeError):
                await make_service(
                    session=session,
                    config=config,
                    clock=fake_clock,
                ).exchange_authorization_code(
                    authorization_code=authorization_code,
                    code_verifier=CODE_VERIFIER,
                )

        async with session_factory() as session:
            assert await session.get(NativeAuthCode, code_digest) is None
    finally:
        if account_id is not None:
            async with session_factory() as session:
                await session.execute(delete_accounts(account_id))
                await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_login_commits", [True, False])
async def test_concurrent_first_login_uses_account_created_by_other_request(
    integration_database_url: str,
    config: Config,
    first_login_commits: bool,
) -> None:
    engine = create_async_engine(integration_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    provider_subject = f"integration-{secrets.token_hex(8)}"
    login_time = datetime(2001, 2, 3, 4, 5, 6, tzinfo=UTC)
    winner = session_factory()
    login_task: asyncio.Task[str] | None = None

    async def login() -> str:
        async with session_factory() as session:
            kakao_client = create_autospec(KakaoClient, instance=True)
            kakao_client.get_user_id.return_value = provider_subject
            service = SignIn(
                session=session,
                oauth_client=kakao_client,
                auth_codes=NativeAuthCodeRepository(),
                config=config,
                clock=FakeClock(login_time),
            )
            return await service.login(
                provider=AccountProvider.KAKAO,
                code="authorization-code",
                code_challenge=CODE_CHALLENGE,
            )

    try:
        await winner.begin()
        winner_account = Account(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
        winner.add(winner_account)
        await winner.flush()
        winner_labels = default_labels(winner_account.id)
        winner.add_all(winner_labels)
        await winner.flush()
        winner_xid = await winner.scalar(text("SELECT pg_current_xact_id()::text"))

        login_task = asyncio.create_task(login())
        async with asyncio.timeout(5), session_factory() as observer:
            while not await observer.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE locktype = 'transactionid' "
                    "AND transactionid::text = :xid AND NOT granted)"
                ),
                {"xid": winner_xid},
            ):
                assert not login_task.done(), login_task.result()
                await asyncio.sleep(0.01)

        if first_login_commits:
            await winner.commit()
        else:
            await winner.rollback()
        authorization_code = await asyncio.wait_for(login_task, 5)

        async with session_factory() as session:
            accounts = (
                await session.scalars(
                    select(Account).where(
                        account_table.c.provider == AccountProvider.KAKAO,
                        account_table.c.provider_subject == provider_subject,
                    )
                )
            ).all()
            assert len(accounts) == 1
            (account,) = accounts
            labels = (
                await session.scalars(
                    select(Label).where(Label.account_id == account.id)
                )
            ).all()
            assert len(labels) == 5
            stored_code = await session.get(
                NativeAuthCode,
                hashlib.sha256(authorization_code.encode()).hexdigest(),
            )
            assert stored_code is not None
            assert stored_code.account_id == account.id
            if first_login_commits:
                assert account.id == winner_account.id
                assert {label.label_id for label in labels} == {
                    label.label_id for label in winner_labels
                }
                assert account.last_authenticated_at == login_time
            else:
                assert account.id != winner_account.id
    finally:
        await winner.rollback()
        await winner.close()
        if login_task is not None and not login_task.done():
            login_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await login_task
        async with session_factory() as session:
            await session.execute(
                delete(account_table).where(
                    account_table.c.provider == AccountProvider.KAKAO,
                    account_table.c.provider_subject == provider_subject,
                )
            )
            await session.commit()
        await engine.dispose()

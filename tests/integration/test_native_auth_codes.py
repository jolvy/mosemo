import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, create_autospec
from uuid import UUID

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.auth.kakao_client import KakaoClient
from mosemo.auth.models import NativeAuthCode
from mosemo.auth.pkce import create_code_challenge
from mosemo.auth.repository import NativeAuthCodeRepository
from mosemo.auth.service import AuthService, InvalidAuthorizationCodeError
from mosemo.auth.tokens import TokenService
from mosemo.config import Config

CODE_VERIFIER = "A" * 43
CODE_CHALLENGE = create_code_challenge(CODE_VERIFIER)


def make_service(
    *,
    session: AsyncSession,
    config: Config,
) -> AuthService:
    return AuthService(
        oauth_client=create_autospec(KakaoClient, instance=True),
        provider=AccountProvider.KAKAO,
        session=session,
        account_repository=AccountRepository(session),
        native_auth_code_repository=NativeAuthCodeRepository(session),
        token_service=TokenService(config.auth),
        config=config.auth,
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
    auth_code_repository.save.side_effect = RuntimeError("code storage failed")
    account_repository = AccountRepository(integration_session)
    service = AuthService(
        oauth_client=kakao_client,
        provider=AccountProvider.KAKAO,
        session=integration_session,
        account_repository=account_repository,
        native_auth_code_repository=auth_code_repository,
        token_service=TokenService(config.auth),
        config=config.auth,
    )

    with pytest.raises(RuntimeError, match="code storage failed"):
        await service.login(
            code="authorization-code",
            code_challenge=CODE_CHALLENGE,
        )

    assert (
        await account_repository.find(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
        is None
    )


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
                last_authenticated_at=initial_last_authenticated_at,
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            account_id = account.account_id
            initial_last_authenticated_at = account.last_authenticated_at

        async with session_factory() as session:
            kakao_client = create_autospec(KakaoClient, instance=True)
            kakao_client.get_user_id.return_value = provider_subject
            auth_code_repository = NativeAuthCodeRepository(session)
            save_auth_code = Mock(
                side_effect=RuntimeError("code storage failed"),
            )
            monkeypatch.setattr(auth_code_repository, "save", save_auth_code)
            service = AuthService(
                oauth_client=kakao_client,
                provider=AccountProvider.KAKAO,
                session=session,
                account_repository=AccountRepository(session),
                native_auth_code_repository=auth_code_repository,
                token_service=TokenService(config.auth),
                config=config.auth,
            )

            with pytest.raises(RuntimeError, match="code storage failed"):
                await service.login(
                    code="authorization-code",
                    code_challenge=CODE_CHALLENGE,
                )

            save_auth_code.assert_called_once()

        async with session_factory() as session:
            stored_account = await AccountRepository(session).find(
                provider=AccountProvider.KAKAO,
                provider_subject=provider_subject,
            )
            assert stored_account is not None
            assert stored_account.account_id == account_id
            assert stored_account.last_authenticated_at == initial_last_authenticated_at
    finally:
        async with session_factory() as session:
            await session.execute(
                delete(Account).where(
                    Account.provider == AccountProvider.KAKAO,
                    Account.provider_subject == provider_subject,
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
            account_id = account.account_id

            NativeAuthCodeRepository(session).save(
                code_digest=code_digest,
                account_id=account_id,
                code_challenge=CODE_CHALLENGE,
                expires_at=datetime.now(UTC) + timedelta(minutes=1),
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
                    ).exchange_authorization_code(
                        authorization_code=authorization_code,
                        code_verifier=CODE_VERIFIER,
                    )
                except InvalidAuthorizationCodeError:
                    return None

        results = await asyncio.gather(exchange(), exchange())

        tokens = [result for result in results if result is not None]
        assert len(tokens) == 1
        assert TokenService(config.auth).decode_access_token(tokens[0]) == account_id
        async with session_factory() as session:
            assert await session.get(NativeAuthCode, code_digest) is None
    finally:
        if account_id is not None:
            async with session_factory() as session:
                await session.execute(
                    delete(Account).where(Account.account_id == account_id)
                )
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
            account_id = account.account_id

            NativeAuthCodeRepository(session).save(
                code_digest=code_digest,
                account_id=account_id,
                code_challenge=CODE_CHALLENGE,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
            await session.commit()

        async with session_factory() as session:
            with pytest.raises(InvalidAuthorizationCodeError):
                await make_service(
                    session=session,
                    config=config,
                ).exchange_authorization_code(
                    authorization_code=authorization_code,
                    code_verifier=CODE_VERIFIER,
                )

        async with session_factory() as session:
            assert await session.get(NativeAuthCode, code_digest) is None
    finally:
        if account_id is not None:
            async with session_factory() as session:
                await session.execute(
                    delete(Account).where(Account.account_id == account_id)
                )
                await session.commit()
        await engine.dispose()

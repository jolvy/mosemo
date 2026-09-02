import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import create_autospec
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
        kakao_client=create_autospec(KakaoClient, instance=True),
        session=session,
        account_repository=AccountRepository(session),
        native_auth_code_repository=NativeAuthCodeRepository(session),
        token_service=TokenService(config.auth),
        config=config.auth,
    )


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

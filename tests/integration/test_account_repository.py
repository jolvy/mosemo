from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.identity.infrastructure.accounts import AccountRepository
from mosemo.shared.domain.timezones import Timezone


@pytest.mark.asyncio
async def test_repository_saves_and_finds_account(
    integration_session: AsyncSession,
) -> None:
    provider_subject = f"integration-{uuid4()}"
    repository = AccountRepository()
    account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=provider_subject,
    )

    assert await repository.find(integration_session, account.id) is None

    integration_session.add(account)
    await integration_session.flush()
    assert account.timezone is Timezone.ASIA_SEOUL

    account.timezone = Timezone.AMERICA_NEW_YORK
    await integration_session.flush()
    await integration_session.refresh(account, ["timezone"])
    assert account.timezone is Timezone.AMERICA_NEW_YORK

    found = await repository.find(integration_session, account.id)
    assert found is account
    await integration_session.commit()


@pytest.mark.asyncio
async def test_repository_enforces_provider_subject_uniqueness(
    integration_session: AsyncSession,
) -> None:
    provider_subject = f"integration-{uuid4()}"

    integration_session.add(
        Account(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
    )
    integration_session.add(
        Account(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
    )

    with pytest.raises(IntegrityError):
        await integration_session.flush()


@pytest.mark.asyncio
async def test_repository_gets_or_creates_account_by_provider_identity(
    integration_session: AsyncSession,
) -> None:
    provider_subject = f"integration-{uuid4()}"
    repository = AccountRepository()

    account, created = await repository.get_or_create(
        integration_session,
        Account(provider=AccountProvider.KAKAO, provider_subject=provider_subject),
    )

    assert created is True
    assert account.id is not None
    assert account.provider is AccountProvider.KAKAO
    assert account.provider_subject == provider_subject
    assert account.timezone is Timezone.ASIA_SEOUL
    assert account in integration_session
    assert await repository.find(integration_session, account.id) is account

    retried, created_again = await repository.get_or_create(
        integration_session,
        Account(provider=AccountProvider.KAKAO, provider_subject=provider_subject),
    )

    assert created_again is False
    assert retried is account

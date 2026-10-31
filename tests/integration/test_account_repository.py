from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.timezones import Timezone


@pytest.mark.asyncio
async def test_repository_saves_and_finds_account(
    integration_session: AsyncSession,
) -> None:
    provider_subject = f"integration-{uuid4()}"
    repository = AccountRepository(integration_session)

    assert (
        await repository.find(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
        is None
    )

    account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=provider_subject,
    )
    repository.add(account)
    await integration_session.flush()
    assert account.timezone is Timezone.ASIA_SEOUL

    account.timezone = Timezone.AMERICA_NEW_YORK
    await integration_session.flush()
    await integration_session.refresh(account, ["timezone"])
    assert account.timezone is Timezone.AMERICA_NEW_YORK

    found = await repository.find(
        provider=AccountProvider.KAKAO,
        provider_subject=provider_subject,
    )
    assert found is account
    await integration_session.commit()


@pytest.mark.asyncio
async def test_repository_enforces_provider_subject_uniqueness(
    integration_session: AsyncSession,
) -> None:
    provider_subject = f"integration-{uuid4()}"
    repository = AccountRepository(integration_session)

    repository.add(
        Account(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
    )
    repository.add(
        Account(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
    )

    with pytest.raises(IntegrityError):
        await integration_session.flush()


@pytest.mark.asyncio
async def test_repository_adds_account_only_if_absent(
    integration_session: AsyncSession,
) -> None:
    provider_subject = f"integration-{uuid4()}"
    repository = AccountRepository(integration_session)

    account = await repository.add_if_absent(
        provider=AccountProvider.KAKAO,
        provider_subject=provider_subject,
    )

    assert account is not None
    assert account.account_id is not None
    assert account.provider is AccountProvider.KAKAO
    assert account.provider_subject == provider_subject
    assert account.timezone is Timezone.ASIA_SEOUL
    assert (
        await repository.find(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
        is account
    )
    assert (
        await repository.add_if_absent(
            provider=AccountProvider.KAKAO,
            provider_subject=provider_subject,
        )
        is None
    )

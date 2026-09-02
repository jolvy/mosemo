from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import AccountProvider
from mosemo.accounts.repository import AccountRepository


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

    account = repository.save(
        provider=AccountProvider.KAKAO,
        provider_subject=provider_subject,
    )
    await integration_session.flush()

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

    repository.save(
        provider=AccountProvider.KAKAO,
        provider_subject=provider_subject,
    )
    repository.save(
        provider=AccountProvider.KAKAO,
        provider_subject=provider_subject,
    )

    with pytest.raises(IntegrityError):
        await integration_session.flush()

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import Account, AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.activity_labels.catalog.models import Label, default_labels
from mosemo.activity_labels.catalog.repository import LabelCatalogRepository


@pytest.mark.asyncio
async def test_label_constraints_are_account_scoped_and_cascade(
    integration_session: AsyncSession,
) -> None:
    account_repository = AccountRepository(integration_session)
    first_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=f"label-{uuid4()}",
    )
    account_repository.add(first_account)
    second_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=f"label-{uuid4()}",
    )
    account_repository.add(second_account)
    await integration_session.flush()

    label_repository = LabelCatalogRepository(integration_session)
    first_labels = default_labels(first_account.account_id)
    label_repository.add_all(first_labels)
    second_labels = default_labels(second_account.account_id)
    label_repository.add_all(second_labels)
    await integration_session.flush()
    assert len(first_labels) == 5
    assert len(second_labels) == 5

    with pytest.raises(IntegrityError):
        async with integration_session.begin_nested():
            integration_session.add(
                Label(
                    account_id=first_account.account_id,
                    display_name="코딩",
                )
            )
            await integration_session.flush()

    first_labels[0].archived_at = first_labels[0].created_at
    await integration_session.flush()

    with pytest.raises(IntegrityError):
        async with integration_session.begin_nested():
            integration_session.add(
                Label(
                    account_id=first_account.account_id,
                    display_name="코딩",
                )
            )
            await integration_session.flush()

    with pytest.raises(IntegrityError):
        async with integration_session.begin_nested():
            integration_session.add(
                Label(
                    account_id=uuid4(),
                    display_name="고아 라벨",
                )
            )
            await integration_session.flush()

    await integration_session.delete(second_account)
    await integration_session.flush()
    remaining_labels = (
        await integration_session.scalars(
            select(Label).where(Label.account_id == second_account.account_id)
        )
    ).all()
    assert remaining_labels == []

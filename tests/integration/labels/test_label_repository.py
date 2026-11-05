from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.labels.domain.labels import Label, default_labels
from mosemo.labels.infrastructure.labels import LabelRepository
from mosemo.labels.infrastructure.tables import labels as label_table


@pytest.mark.asyncio
async def test_label_constraints_are_account_scoped_and_cascade(
    integration_session: AsyncSession,
) -> None:
    first_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=f"label-{uuid4()}",
    )
    integration_session.add(first_account)
    second_account = Account(
        provider=AccountProvider.KAKAO,
        provider_subject=f"label-{uuid4()}",
    )
    integration_session.add(second_account)
    await integration_session.flush()

    label_repository = LabelRepository()
    first_labels = default_labels(first_account.id)
    label_repository.add_all(integration_session, first_labels)
    second_labels = default_labels(second_account.id)
    label_repository.add_all(integration_session, second_labels)
    await integration_session.flush()
    assert len(first_labels) == 5
    assert len(second_labels) == 5

    savepoint = await integration_session.begin_nested()
    integration_session.add(
        Label(
            account_id=first_account.id,
            display_name="코딩",
        )
    )
    with pytest.raises(IntegrityError):
        await integration_session.flush()
    await savepoint.rollback()

    first_labels[0].archived_at = first_labels[0].created_at
    await integration_session.flush()

    savepoint = await integration_session.begin_nested()
    integration_session.add(
        Label(
            account_id=first_account.id,
            display_name="코딩",
        )
    )
    with pytest.raises(IntegrityError):
        await integration_session.flush()
    await savepoint.rollback()

    savepoint = await integration_session.begin_nested()
    integration_session.add(
        Label(
            account_id=uuid4(),
            display_name="고아 라벨",
        )
    )
    with pytest.raises(IntegrityError):
        await integration_session.flush()
    await savepoint.rollback()

    await integration_session.delete(second_account)
    await integration_session.flush()
    remaining_labels = (
        await integration_session.scalars(
            select(Label).where(label_table.c.account_id == second_account.id)
        )
    ).all()
    assert remaining_labels == []

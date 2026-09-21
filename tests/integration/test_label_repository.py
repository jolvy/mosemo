from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.accounts.models import AccountProvider
from mosemo.accounts.repository import AccountRepository
from mosemo.labels.models import Label
from mosemo.labels.repository import LabelRepository


@pytest.mark.asyncio
async def test_label_constraints_are_account_scoped_and_cascade(
    integration_session: AsyncSession,
) -> None:
    account_repository = AccountRepository(integration_session)
    first_account = account_repository.save(
        provider=AccountProvider.KAKAO,
        provider_subject=f"label-{uuid4()}",
    )
    second_account = account_repository.save(
        provider=AccountProvider.KAKAO,
        provider_subject=f"label-{uuid4()}",
    )
    await integration_session.flush()

    label_repository = LabelRepository(integration_session)
    first_labels = label_repository.create_defaults(
        account_id=first_account.account_id,
    )
    second_labels = label_repository.create_defaults(
        account_id=second_account.account_id,
    )
    await integration_session.flush()
    assert len(first_labels) == 5
    assert len(second_labels) == 5

    with pytest.raises(IntegrityError):
        async with integration_session.begin_nested():
            integration_session.add(
                Label(
                    account_id=first_account.account_id,
                    display_name="다른 표시 이름",
                    name_key="다른 표시 이름",
                    default_key="coding",
                )
            )
            await integration_session.flush()

    with pytest.raises(IntegrityError):
        async with integration_session.begin_nested():
            integration_session.add(
                Label(
                    account_id=uuid4(),
                    display_name="고아 라벨",
                    name_key="고아 라벨",
                    default_key=None,
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

    with pytest.raises(IntegrityError):
        async with integration_session.begin_nested():
            integration_session.add(
                Label(
                    account_id=first_account.account_id,
                    display_name="다른 표시 이름",
                    name_key="코딩",
                    default_key=None,
                )
            )
            await integration_session.flush()

"""LabelCatalog, the labels public collaborator (#150)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.identity.domain.accounts import Account, AccountProvider
from mosemo.labels.bootstrap import create_label_catalog
from mosemo.labels.domain.labels import Label
from mosemo.labels.public import ActiveLabel

ARCHIVED_AT = datetime(2026, 9, 14, 2, tzinfo=UTC)


async def add_account(session: AsyncSession) -> Account:
    account = Account(provider=AccountProvider.KAKAO, provider_subject=str(uuid4()))
    session.add(account)
    await session.flush()
    return account


@pytest.mark.asyncio
async def test_label_catalog_reads_only_the_account_labels(
    integration_session: AsyncSession,
) -> None:
    owner = await add_account(integration_session)
    other = await add_account(integration_session)
    active = Label(account_id=owner.id, display_name="독서")
    archived = Label(account_id=owner.id, display_name="산책", archived_at=ARCHIVED_AT)
    foreign = Label(account_id=other.id, display_name="다른 계정")
    integration_session.add_all([active, archived, foreign])
    await integration_session.flush()
    catalog = create_label_catalog()

    async def is_active(label: Label) -> bool:
        return await catalog.is_active(
            integration_session, account_id=owner.id, label_id=label.id
        )

    assert [await is_active(label) for label in (active, archived, foreign)] == [
        True,
        False,
        False,
    ]
    assert await catalog.list_active(integration_session, owner.id) == [
        ActiveLabel(id=active.id, display_name="독서")
    ]
    missing = uuid4()
    assert await catalog.find_names(
        integration_session,
        account_id=owner.id,
        label_ids={active.id, archived.id, foreign.id, missing},
    ) == {active.id: "독서", archived.id: "산책"}
    assert (
        await catalog.find_names(
            integration_session, account_id=owner.id, label_ids=set()
        )
        == {}
    )

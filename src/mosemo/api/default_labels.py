"""Temporary `AccountCreated` handler that keeps default labels at sign-up.

It wraps the old label catalog code until the labels context subscribes with
its own handler (#150). Identity no longer creates labels itself.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.catalog.models import default_labels
from mosemo.activity_labels.catalog.repository import LabelCatalogRepository
from mosemo.identity.public import AccountCreated
from mosemo.shared.application.events import EventDispatcher


async def create_default_labels(
    event: AccountCreated, db_session: AsyncSession
) -> None:
    LabelCatalogRepository(db_session).add_all(default_labels(event.account_id))


def register_default_labels(dispatcher: EventDispatcher) -> None:
    dispatcher.subscribe(AccountCreated, create_default_labels)

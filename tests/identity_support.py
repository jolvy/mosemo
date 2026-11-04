"""Identity test helpers: cleanup statements and the sign-up event dispatcher."""

from uuid import UUID

from sqlalchemy import Delete, delete

from mosemo.identity.infrastructure.tables import accounts
from mosemo.labels import bootstrap as labels_bootstrap
from mosemo.shared.application.events import EventDispatcher


def delete_accounts(*account_ids: UUID) -> Delete:
    """Delete the accounts; their devices, codes, and labels cascade."""
    return delete(accounts).where(accounts.c.id.in_(account_ids))


def sign_up_dispatcher() -> EventDispatcher:
    """A dispatcher with the labels handlers that the API registers."""
    dispatcher = EventDispatcher()
    labels_bootstrap.register_handlers(dispatcher)
    return dispatcher

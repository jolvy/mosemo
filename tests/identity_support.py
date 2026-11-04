"""Identity test helpers: cleanup statements and the sign-up event dispatcher."""

from uuid import UUID

from sqlalchemy import Delete, delete

from mosemo.api.default_labels import register_default_labels
from mosemo.identity.infrastructure.tables import accounts
from mosemo.shared.application.events import EventDispatcher


def delete_accounts(*account_ids: UUID) -> Delete:
    """Delete the accounts; their devices, codes, and labels cascade."""
    return delete(accounts).where(accounts.c.id.in_(account_ids))


def sign_up_dispatcher() -> EventDispatcher:
    """A dispatcher with the temporary default label handler the API registers."""
    dispatcher = EventDispatcher()
    register_default_labels(dispatcher)
    return dispatcher

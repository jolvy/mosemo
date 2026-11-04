"""Labels assembly for the assembly roots: mapping, handlers, object graph, router."""

from fastapi import APIRouter

from mosemo.identity.public import AccountCreated, AccountDirectory
from mosemo.labels.application.catalog import LabelCatalog
from mosemo.labels.application.labels import DefaultLabels, ListLabels
from mosemo.labels.infrastructure.labels import LabelRepository
from mosemo.labels.infrastructure.queries import LabelQuery
from mosemo.labels.infrastructure.tables import map_tables
from mosemo.labels.presentation.error_docs import ERROR_DOCS
from mosemo.labels.presentation.router import create_router
from mosemo.shared.application.events import EventDispatcher

__all__ = [
    "ERROR_DOCS",
    "Labels",
    "create_label_catalog",
    "map_tables",
    "register_handlers",
]


def register_handlers(dispatcher: EventDispatcher) -> None:
    """Subscribe the labels handlers; sign-up creates the default labels."""
    default_labels = DefaultLabels(labels=LabelRepository())
    dispatcher.subscribe(AccountCreated, default_labels.on_account_created)


def create_label_catalog() -> LabelCatalog:
    return LabelCatalog(labels=LabelQuery())


class Labels:
    """Stateless labels objects built once when an assembly root starts."""

    def __init__(self, *, accounts: AccountDirectory) -> None:
        self.label_catalog = create_label_catalog()
        self.list_labels = ListLabels(labels=LabelRepository(), accounts=accounts)

    def router(self) -> APIRouter:
        return create_router(list_labels=self.list_labels)

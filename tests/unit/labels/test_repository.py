from unittest.mock import create_autospec
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activity_labels.catalog.models import DEFAULT_LABEL_NAMES, default_labels
from mosemo.activity_labels.catalog.repository import LabelCatalogRepository


def test_add_all_adds_the_default_catalog_for_an_account() -> None:
    session = create_autospec(AsyncSession, instance=True)
    repository = LabelCatalogRepository(session)
    account_id = uuid4()

    labels = default_labels(account_id)
    repository.add_all(labels)

    assert [label.display_name for label in labels] == list(DEFAULT_LABEL_NAMES)
    assert all(not hasattr(label, "name_key") for label in labels)
    assert all(not hasattr(label, "default_key") for label in labels)
    assert all(label.account_id == account_id for label in labels)
    session.add_all.assert_called_once_with(labels)

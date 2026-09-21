from unittest.mock import create_autospec
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labels.models import DEFAULT_LABELS
from mosemo.labels.repository import LabelRepository


def test_create_defaults_adds_the_catalog_for_an_account() -> None:
    session = create_autospec(AsyncSession, instance=True)
    repository = LabelRepository(session)
    account_id = uuid4()

    labels = repository.create_defaults(account_id=account_id)

    assert [label.display_name for label in labels] == [
        "코딩",
        "학습",
        "소통",
        "쇼핑",
        "여가",
    ]
    assert [label.default_key for label in labels] == [
        default.default_key for default in DEFAULT_LABELS
    ]
    assert all(label.account_id == account_id for label in labels)
    session.add_all.assert_called_once_with(labels)

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labels.models import DEFAULT_LABEL_NAMES, Label


class LabelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def create_defaults(self, *, account_id: UUID) -> list[Label]:
        labels = [
            Label(
                account_id=account_id,
                display_name=display_name,
            )
            for display_name in DEFAULT_LABEL_NAMES
        ]
        self._session.add_all(labels)
        return labels

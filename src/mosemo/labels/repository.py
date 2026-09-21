from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.labels.models import DEFAULT_LABELS, Label, normalize_label_name


class LabelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def create_defaults(self, *, account_id: UUID) -> list[Label]:
        labels = [
            Label(
                account_id=account_id,
                display_name=default.display_name,
                name_key=normalize_label_name(default.display_name),
                default_key=default.default_key,
            )
            for default in DEFAULT_LABELS
        ]
        self._session.add_all(labels)
        return labels

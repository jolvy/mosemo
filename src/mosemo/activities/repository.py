from typing import Never
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.activities.schemas import ActivityRecord


class ActivityStorageNotImplementedError(Exception):
    pass


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        *,
        account_id: UUID,
        record: ActivityRecord,
    ) -> Never:
        raise ActivityStorageNotImplementedError

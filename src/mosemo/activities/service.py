from typing import Never
from uuid import UUID

from mosemo.activities.repository import ActivityRepository
from mosemo.activities.schemas import ActivityRecord


class ActivityService:
    def __init__(self, repository: ActivityRepository) -> None:
        self._repository = repository

    async def create_activity(
        self,
        *,
        account_id: UUID,
        record: ActivityRecord,
    ) -> Never:
        return await self._repository.save(
            account_id=account_id,
            record=record,
        )

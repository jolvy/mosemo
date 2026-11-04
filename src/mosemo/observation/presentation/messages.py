"""Internal activity batch contract, independent of the HTTP envelope."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from mosemo.observation.application.batches import ActivityBatch
from mosemo.observation.presentation.activities.mappers import activity_batch
from mosemo.observation.presentation.activities.schemas import (
    ActivityBatchRequest,
    BatchRecord,
)


class ActivityRegistrationRequested(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    account_id: UUID
    batch_id: UUID
    device_id: UUID
    records: list[BatchRecord] = Field(min_length=1, max_length=200)

    @classmethod
    def from_batch(
        cls, account_id: UUID, batch: ActivityBatchRequest
    ) -> ActivityRegistrationRequested:
        return cls(
            account_id=account_id,
            batch_id=batch.batch_id,
            device_id=batch.device_id,
            records=batch.records,
        )

    def to_activity_batch(self) -> ActivityBatch:
        """The batch to process, fingerprinted as its HTTP request would be."""
        return activity_batch(
            ActivityBatchRequest.model_validate(
                {
                    "batchId": self.batch_id,
                    "deviceId": self.device_id,
                    "records": self.records,
                }
            )
        )

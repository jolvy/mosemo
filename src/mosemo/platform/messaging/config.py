from pydantic import BaseModel, Field


class BatchDeliveryConfig(BaseModel):
    """Shared Kafka configuration for API publication and batch consumption."""

    enabled: bool = True
    topic: str = Field(default="activities.registration-requested", min_length=1)
    bootstrap_servers: str = Field(default="localhost:9092", min_length=1)
    group_id: str = Field(default="mosemo.activity-batches", min_length=1)

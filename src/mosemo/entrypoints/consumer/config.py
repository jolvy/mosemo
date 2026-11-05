"""Settings that the Consumer process reads and validates at startup."""

from functools import cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from mosemo.platform.messaging.config import BatchDeliveryConfig


class ConsumerConfig(BaseSettings):
    batch_delivery: BatchDeliveryConfig = Field(
        default_factory=BatchDeliveryConfig, validation_alias="BATCH"
    )

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        env_nested_delimiter="_",
        env_nested_max_split=1,
        populate_by_name=True,
    )


@cache
def get_consumer_config() -> ConsumerConfig:
    return ConsumerConfig()

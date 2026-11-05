"""Settings that the batch job process reads and validates at startup."""

from functools import cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BatchJobsConfig(BaseSettings):
    label_model: str = Field(min_length=1, validation_alias="LABEL_MODEL")

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        populate_by_name=True,
    )


@cache
def get_batch_jobs_config() -> BatchJobsConfig:
    return BatchJobsConfig()

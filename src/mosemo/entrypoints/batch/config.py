"""Settings that the batch job process reads and validates at startup."""

from functools import cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from mosemo.analytics.bootstrap import AnalyticsBatchConfig

DEFAULT_REPROCESS_PER_RUN = 600  # an assumption until measured (ADR 0016)


class BatchJobsConfig(BaseSettings):
    label_model: str = Field(min_length=1, validation_alias="LABEL_MODEL")
    analytics_reprocess_per_run: int = Field(
        DEFAULT_REPROCESS_PER_RUN, ge=0, validation_alias="ANALYTICS_REPROCESS_PER_RUN"
    )
    # Test-only: comma-separated wait point names and the directory of their
    # signal files. Production settings never set them.
    analytics_test_wait_points: str = Field(
        "", validation_alias="ANALYTICS_TEST_WAIT_POINTS"
    )
    analytics_test_wait_directory: Path | None = Field(
        None, validation_alias="ANALYTICS_TEST_WAIT_DIRECTORY"
    )

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    def analytics(self) -> AnalyticsBatchConfig:
        names = self.analytics_test_wait_points.split(",")
        return AnalyticsBatchConfig(
            reprocess_per_run=self.analytics_reprocess_per_run,
            test_wait_points=frozenset(name.strip() for name in names if name.strip()),
            test_wait_directory=self.analytics_test_wait_directory,
        )


@cache
def get_batch_jobs_config() -> BatchJobsConfig:
    return BatchJobsConfig()

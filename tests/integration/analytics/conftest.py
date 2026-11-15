from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from tests.support.analytics_batch import BatchWorld, create_database


@pytest.fixture(scope="session")
def analytics_batch_database_url(integration_database_url: str) -> str:
    """A database of the batch tests alone: the claim sees every account."""
    return create_database(integration_database_url, "analytics_batch")


@pytest_asyncio.fixture
async def world(
    analytics_batch_database_url: str, tmp_path
) -> AsyncIterator[BatchWorld]:
    world = BatchWorld(analytics_batch_database_url, tmp_path)
    try:
        yield world
    finally:
        await world.close()
        await world.reset()

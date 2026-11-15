from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mosemo.analytics.infrastructure.tables import analytics_definitions

DEFINITION_ROW_ID = 1


class AnalyticsDefinitionQuery:
    """The metric definition version recorded in the database."""

    async def read(self, db_session: AsyncSession) -> int:
        version = await db_session.scalar(
            select(analytics_definitions.c.version).where(
                analytics_definitions.c.id == DEFINITION_ROW_ID
            )
        )
        assert version is not None
        return version

    async def read_for_share(self, db_session: AsyncSession) -> int:
        """The version, holding a FOR SHARE lock until the transaction ends so a
        definition change cannot commit under the caller."""
        version = await db_session.scalar(
            select(analytics_definitions.c.version)
            .where(analytics_definitions.c.id == DEFINITION_ROW_ID)
            .with_for_update(read=True)
        )
        assert version is not None
        return version

import asyncio
from collections.abc import Iterator
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from mosemo.labeling import bootstrap as labeling_bootstrap
from mosemo.observation import bootstrap as observation_bootstrap
from tests.support.seed_database import SeedDatabase

# The timeline reads go through the observation repositories, so map their entities.
observation_bootstrap.map_tables()
labeling_bootstrap.map_tables()

SEED_LOAD = Path(__file__).resolve().parents[3] / "scripts/seed_load.py"


@pytest.fixture(scope="session")
def seed_load() -> ModuleType:
    spec = spec_from_file_location("seed_load", SEED_LOAD)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _execute_autocommit(url: URL, statement: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def seed_database_url(integration_database_url: str) -> Iterator[URL]:
    """A migrated database of its own: the loader commits, unlike other tests."""
    url = make_url(integration_database_url)
    name = f"seed_load_{uuid4().hex}"
    asyncio.run(_execute_autocommit(url, f'CREATE DATABASE "{name}"'))
    seed_url = url.set(database=name)
    try:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", seed_url.render_as_string(hide_password=False)
        )
        command.upgrade(config, "head")
        yield seed_url
    finally:
        asyncio.run(_execute_autocommit(url, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
def seed_database(
    seed_load: ModuleType, seed_database_url: URL, monkeypatch: pytest.MonkeyPatch
) -> SeedDatabase:
    """An emptied seed database with the DB_* environment pointing at it."""
    database = SeedDatabase(module=seed_load, url=seed_database_url)
    database.execute(f"TRUNCATE {', '.join(seed_load.TABLE_ORDER)} CASCADE")
    for name, value in (
        ("DB_DATABASE", seed_database_url.database),
        ("DB_USER", seed_database_url.username),
        ("DB_PASSWORD", seed_database_url.password),
        ("DB_HOST", seed_database_url.host),
        ("DB_PORT", seed_database_url.port),
    ):
        assert value is not None
        monkeypatch.setenv(name, str(value))
    return database

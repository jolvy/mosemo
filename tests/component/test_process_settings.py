"""Each process validates only its own settings, when it starts."""

import argparse
import os
import runpy
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from mosemo.entrypoints.batch.config import get_batch_jobs_config
from mosemo.entrypoints.consumer.config import get_consumer_config
from mosemo.platform.database.config import get_database_settings

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PREFIXES = ("DB_", "BATCH_", "MOSEMO_ENV", "KAKAO_", "AUTH_", "LABEL_MODEL")
API_ENVIRONMENT = runpy.run_path(str(ROOT / "scripts/export_openapi.py"))[
    "EXPORT_ENVIRONMENT"
]
DATABASE_ENVIRONMENT = {
    name: value for name, value in API_ENVIRONMENT.items() if name.startswith("DB_")
}


def only(values: dict[str, str]) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(SETTINGS_PREFIXES)
    }
    return environment | values


def run_python(*arguments: str, environment: dict[str, str]):
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture(autouse=True)
def fresh_settings():
    for getter in (get_database_settings, get_consumer_config, get_batch_jobs_config):
        getter.cache_clear()
    yield
    for getter in (get_database_settings, get_consumer_config, get_batch_jobs_config):
        getter.cache_clear()


def test_migrations_run_with_only_db_variables() -> None:
    # Offline (--sql) mode loads env.py and the DSN without a database; stop at
    # the first revision because later ones backfill data from a live connection.
    result = run_python(
        "-m",
        "alembic",
        "upgrade",
        "44f2c8ca846b",
        "--sql",
        environment=only(DATABASE_ENVIRONMENT),
    )

    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE" in result.stdout


@pytest.mark.parametrize(
    "missing", ["MOSEMO_ENV", "KAKAO_REST_API_KEY", "AUTH_JWT_SECRET_KEY", "DB_HOST"]
)
def test_api_fails_at_startup_without_a_required_variable(missing: str) -> None:
    values = {name: value for name, value in API_ENVIRONMENT.items() if name != missing}

    result = run_python(
        "-c", "import mosemo.entrypoints.api.main", environment=only(values)
    )

    assert result.returncode != 0
    assert "ValidationError" in result.stderr


def test_api_starts_with_its_variables() -> None:
    result = run_python(
        "-c", "import mosemo.entrypoints.api.main", environment=only(API_ENVIRONMENT)
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("label_model", [None, ""])
async def test_batch_rejects_label_model_before_building_jobs(
    monkeypatch: pytest.MonkeyPatch, label_model: str | None
) -> None:
    from mosemo.entrypoints.batch.__main__ import run

    for name, value in DATABASE_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("LABEL_MODEL", raising=False)
    if label_model is not None:
        monkeypatch.setenv("LABEL_MODEL", label_model)
    build_jobs = Mock()
    get_engine = Mock()
    monkeypatch.setattr("mosemo.entrypoints.batch.registry.build_jobs", build_jobs)
    monkeypatch.setattr("mosemo.platform.database.engine.get_engine", get_engine)

    with pytest.raises(ValidationError):
        await run(argparse.Namespace(command="serve"))

    build_jobs.assert_not_called()
    get_engine.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["consumer", "batch"])
async def test_entrypoint_rejects_missing_db_variables_at_startup(
    monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    from mosemo.entrypoints.batch.__main__ import run as run_batch
    from mosemo.entrypoints.consumer.__main__ import run as run_consumer

    for name in list(os.environ):
        if name.startswith("DB_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LABEL_MODEL", "openai:fake-model")
    get_engine = Mock()
    monkeypatch.setattr("mosemo.platform.database.engine.get_engine", get_engine)

    start = (
        run_consumer()
        if entrypoint == "consumer"
        else run_batch(argparse.Namespace(command="serve"))
    )

    with pytest.raises(ValidationError):
        await start

    get_engine.assert_not_called()

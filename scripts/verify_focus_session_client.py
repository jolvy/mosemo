"""Verify the Swift client against an isolated API, PostgreSQL, and Kafka.

Run with uv run python scripts/verify_focus_session_client.py --client-root PATH.
Never uses the configured application database or user credentials.
"""

import argparse
import asyncio
import json
import os
import runpy
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from testcontainers.community.kafka import KafkaContainer
from testcontainers.community.postgres import PostgresContainer

# This is a repository verification tool; share the test-only Admin boundary.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.kafka_support import KafkaTestAdmin


async def prepare_topic(bootstrap_servers: str, topic: str) -> None:
    async with KafkaTestAdmin(bootstrap_servers) as admin:
        await admin.prepare_topic(topic)


async def process_published_batches(url: str, bootstrap_servers: str, topic: str):
    """Drain a finite snapshot through the registered FastStream Consumer."""
    from uuid import uuid4

    from faststream.kafka import KafkaBroker
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from mosemo.activities.executor import BatchProcessingExecutor
    from mosemo.clock import utc_now
    from mosemo.infra.batch_consumer import ActivityBatchConsumer

    engine = create_async_engine(url)
    group = "test.focus-verifier." + uuid4().hex
    stop = asyncio.Event()
    task = None
    try:
        async with KafkaTestAdmin(bootstrap_servers) as admin:
            try:
                ends = await admin.end_offsets(topic)
                broker = KafkaBroker(
                    bootstrap_servers,
                    logger=None,
                    graceful_timeout=0,
                    consumer_only=True,
                )
                worker = ActivityBatchConsumer(
                    broker,
                    BatchProcessingExecutor(async_sessionmaker(engine), clock=utc_now),
                )
                task = asyncio.create_task(worker.run(topic, stop, group_id=group))
                async with asyncio.timeout(30):
                    while True:
                        if task.done():
                            task.result()
                            raise RuntimeError("verifier Consumer stopped before drain")
                        offsets = await admin.offsets(group)
                        if all(offsets.get(tp, 0) >= end for tp, end in ends.items()):
                            break
                        await asyncio.sleep(0.05)
            finally:
                stop.set()
                try:
                    if task is not None:
                        await asyncio.wait_for(task, 35)
                finally:
                    await admin.delete_group(group)
    finally:
        await engine.dispose()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--client-root", type=Path, required=True)
args = parser.parse_args()
client_root = args.client_root.resolve()
if not (client_root / "Package.swift").is_file():
    parser.error("client-root must contain the Mosemo macOS Package.swift")
with socket.socket() as port_socket:
    port_socket.bind(("127.0.0.1", 0))
    api_port = port_socket.getsockname()[1]
api_url = f"http://127.0.0.1:{api_port}"
os.environ.update(runpy.run_path("scripts/export_openapi.py")["EXPORT_ENVIRONMENT"])
with (
    PostgresContainer("postgres:18", driver="asyncpg") as database,
    KafkaContainer("confluentinc/cp-kafka:7.8.0")
    .with_kraft()
    .with_env("KAFKA_AUTO_CREATE_TOPICS_ENABLE", "false") as kafka,
):
    url = database.get_connection_url()
    parsed = urlparse(url)
    assert parsed.username and parsed.password and parsed.hostname and parsed.port
    os.environ.update(
        DB_USER=parsed.username,
        DB_PASSWORD=parsed.password,
        DB_HOST=parsed.hostname,
        DB_PORT=str(parsed.port),
        DB_DATABASE=parsed.path.lstrip("/"),
        BATCH_ENABLED="true",
        BATCH_BOOTSTRAP_SERVERS=kafka.get_bootstrap_server(),
        BATCH_TOPIC="test.focus-session-client",
    )
    asyncio.run(
        prepare_topic(os.environ["BATCH_BOOTSTRAP_SERVERS"], os.environ["BATCH_TOPIC"])
    )
    from alembic import command
    from alembic.config import Config as AlembicConfig

    config = AlembicConfig("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    from uuid import uuid4

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from mosemo.accounts.models import Account, AccountProvider
    from mosemo.accounts.repository import AccountRepository
    from mosemo.activities.models import ActivityRecord
    from mosemo.activity_labels.catalog.models import Label
    from mosemo.auth.tokens import TokenService
    from mosemo.clock import utc_now
    from mosemo.config import get_config
    from mosemo.devices.models import Device

    async def seed():
        engine = create_async_engine(url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            account = Account(
                provider=AccountProvider.KAKAO, provider_subject=str(uuid4())
            )
            AccountRepository(session).add(account)
            await session.flush()
            device = Device(account_id=account.account_id, idempotency_key=uuid4())
            label = Label(account_id=account.account_id, display_name="집중 테스트")
            session.add_all([device, label])
            await session.commit()
            result = {
                "url": api_url,
                "token": TokenService(get_config().auth).issue_access_token(
                    account.account_id, now=utc_now()
                ),
                "deviceID": str(device.device_id),
                "labelID": str(label.label_id),
            }
        await engine.dispose()
        return result

    fixture = asyncio.run(seed())
    with tempfile.TemporaryDirectory(prefix="focus-api-e2e-") as folder:
        path = Path(folder) / "fixture.json"
        path.write_text(json.dumps(fixture))
        path.chmod(0o600)
        with open(Path(folder) / "server.log", "w") as output:
            server = subprocess.Popen(
                [
                    "uv",
                    "run",
                    "uvicorn",
                    "mosemo.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
            )
            try:
                for attempt in range(100):
                    if server.poll() is not None:
                        raise RuntimeError(
                            "E2E server stopped; inspect "
                            + str(Path(folder) / "server.log")
                        )
                    try:
                        urllib.request.urlopen(api_url + "/openapi.json", timeout=1)
                        break
                    except urllib.error.URLError, TimeoutError:
                        time.sleep(0.1)
                env = os.environ.copy()
                env.update(
                    DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer",
                    MOSEMO_FOCUS_E2E_FIXTURE=str(path),
                )
                result = subprocess.run(
                    [
                        "swift",
                        "test",
                        "--filter",
                        "testFocusSessionLiveServerLifecycleAndActivityLink",
                    ],
                    cwd=client_root,
                    env=env,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError("Swift live API test failed")

                asyncio.run(
                    process_published_batches(
                        url,
                        os.environ["BATCH_BOOTSTRAP_SERVERS"],
                        os.environ["BATCH_TOPIC"],
                    )
                )

                async def verify():
                    engine = create_async_engine(url)
                    async with async_sessionmaker(engine)() as session:
                        record = await session.scalar(select(ActivityRecord))
                        assert (
                            record is not None and record.focus_session_id is not None
                        )
                        print(
                            "PostgreSQL verification: activity record links to focus session"
                        )
                    await engine.dispose()

                asyncio.run(verify())
            finally:
                server.terminate()
                server.wait(timeout=10)

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
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.structs import TopicPartition
from testcontainers.community.kafka import KafkaContainer
from testcontainers.community.postgres import PostgresContainer


async def prepare_topic(bootstrap_servers: str, topic: str) -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
    try:
        await admin.start()
        await admin.create_topics(
            [NewTopic(topic, num_partitions=2, replication_factor=1)]
        )
    finally:
        await admin.close()


async def process_published_batches(url: str, bootstrap_servers: str, topic: str):
    """Process this finished test's publications, without a production worker."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from mosemo.activities.executor import BatchProcessingExecutor
    from mosemo.activities.messages import ActivityRegistrationRequested

    engine = create_async_engine(url)
    executor = BatchProcessingExecutor(async_sessionmaker(engine))
    consumer = AIOKafkaConsumer(
        bootstrap_servers=bootstrap_servers, group_id=None, enable_auto_commit=False
    )
    try:
        async with asyncio.timeout(30):
            await consumer.start()
            partitions = [TopicPartition(topic, index) for index in range(2)]
            consumer.assign(partitions)
            await consumer.seek_to_beginning()
            ends = await consumer.end_offsets(partitions)
            for partition, end in ends.items():
                while await consumer.position(partition) < end:
                    record = await consumer.getone(partition)
                    assert record.value is not None
                    message = ActivityRegistrationRequested.model_validate_json(
                        record.value
                    )
                    assert record.key == str(message.account_id).encode("utf-8")
                    await executor.execute(message)
    finally:
        await consumer.stop()
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

    from mosemo.accounts.models import AccountProvider
    from mosemo.accounts.repository import AccountRepository
    from mosemo.activities.models import ActivityRecord
    from mosemo.activity_labels.catalog.models import Label
    from mosemo.auth.tokens import TokenService
    from mosemo.config import get_config
    from mosemo.devices.models import Device

    async def seed():
        engine = create_async_engine(url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            account = AccountRepository(session).save(
                provider=AccountProvider.KAKAO, provider_subject=str(uuid4())
            )
            await session.flush()
            device = Device(account_id=account.account_id, idempotency_key=uuid4())
            label = Label(account_id=account.account_id, display_name="집중 테스트")
            session.add_all([device, label])
            await session.commit()
            result = {
                "url": api_url,
                "token": TokenService(get_config().auth).issue_access_token(
                    account.account_id
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

"""The E2E system: the real API and Consumer processes and the batch job process.

Every process gets the same settings: the CI Tests job names with the test
values of `ApiConfig`, the test database and Kafka, and `LABEL_MODEL`.
Inherited application settings are dropped so a local `.env` or a provider key
cannot leak in; nothing calls an outside network.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx2
from sqlalchemy import make_url

from mosemo.entrypoints.api.config import ApiConfig
from mosemo.platform.messaging.config import BatchDeliveryConfig

ROOT = Path(__file__).resolve().parents[2]
SETTING_PREFIXES = (
    "MOSEMO_",
    "DB_",
    "KAKAO_",
    "AUTH_",
    "BATCH_",
    "ANALYTICS_",
    "LABEL_MODEL",
    "OPENAI_",
    "GOOGLE_",
)
READY_TIMEOUT = 30
STOP_TIMEOUT = 30
BATCH_TIMEOUT = 60


def process_environment(
    database_url: str, api_config: ApiConfig, kafka: BatchDeliveryConfig
) -> dict[str, str]:
    url = make_url(database_url)
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(SETTING_PREFIXES)
    }
    environment.update(
        MOSEMO_ENV=api_config.app_env,
        DB_DATABASE=str(url.database),
        DB_USER=str(url.username),
        DB_PASSWORD=str(url.password),
        DB_HOST=str(url.host),
        DB_PORT=str(url.port),
        KAKAO_REST_API_KEY=api_config.kakao.rest_api_key,
        KAKAO_CLIENT_SECRET=api_config.kakao.client_secret.get_secret_value(),
        KAKAO_REDIRECT_URI=api_config.kakao.redirect_uri,
        AUTH_JWT_SECRET_KEY=api_config.auth.jwt_secret_key.get_secret_value(),
        AUTH_MACOS_CALLBACK_URI=api_config.auth.macos_callback_uri,
        LABEL_MODEL="openai:fake-model",
        BATCH_ENABLED="true",
        BATCH_TOPIC=kafka.topic,
        BATCH_BOOTSTRAP_SERVERS=kafka.bootstrap_servers,
        BATCH_GROUP_ID=kafka.group_id,
    )
    return environment


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def service(
    name: str,
    arguments: list[str],
    environment: dict[str, str],
    logs: Path,
    *,
    clean_exits: tuple[int, ...] = (0,),
) -> Iterator[subprocess.Popen[bytes]]:
    """Run `python -m …` until the block ends, then SIGTERM it and wait.

    Output goes to a log file so a full pipe cannot block the process.
    """
    log = logs / f"{name}.log"
    with log.open("wb") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", *arguments],
            cwd=ROOT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            yield process
        finally:
            process.send_signal(signal.SIGTERM)
            try:
                code = process.wait(STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise AssertionError(f"{name} ignored SIGTERM:\n{log.read_text()}")
    assert code in clean_exits, f"{name} exited with {code}:\n{log.read_text()}"


def wait_until_serving(
    process: subprocess.Popen[bytes], base_url: str, log: Path
) -> None:
    """There is no health route; the public OpenAPI document answers when ready."""
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(
                f"the API exited with {process.returncode}:\n{log.read_text()}"
            )
        try:
            if httpx2.get(f"{base_url}/openapi.json", timeout=1).status_code == 200:
                return
        except httpx2.TransportError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"the API did not answer in time:\n{log.read_text()}")


@dataclass(frozen=True)
class E2eSystem:
    base_url: str
    environment: dict[str, str]

    def run_batch_job(self, job_name: str) -> None:
        """Run one batch job once (`run`, never `serve`) and expect exit code 0."""
        result = subprocess.run(
            [sys.executable, "-m", "mosemo.entrypoints.batch", "run", job_name],
            cwd=ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=BATCH_TIMEOUT,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


@contextmanager
def running_system(environment: dict[str, str], logs: Path) -> Iterator[E2eSystem]:
    """The Consumer and the API on a free loopback port, ready for HTTP."""
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    api_command = [
        "uvicorn",
        "mosemo.entrypoints.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--lifespan",
        "on",
    ]
    with ExitStack() as stack:
        stack.enter_context(
            service("consumer", ["mosemo.entrypoints.consumer"], environment, logs)
        )
        # Uvicorn re-raises SIGTERM once its graceful shutdown completes.
        api = stack.enter_context(
            service(
                "api",
                api_command,
                environment,
                logs,
                clean_exits=(0, -signal.SIGTERM),
            )
        )
        wait_until_serving(api, base_url, logs / "api.log")
        yield E2eSystem(base_url=base_url, environment=environment)

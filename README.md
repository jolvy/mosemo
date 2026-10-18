# Mosemo

Project layout and module responsibilities: [ARCHITECTURE.md](ARCHITECTURE.md).

## Development

Install the project and development tools:

```sh
uv sync
uv run pre-commit install
```

Lint and automatically fix safe violations:

```sh
uv run ruff check --fix .
```

Format the code:

```sh
uv run ruff format .
```

Type-check the project:

```sh
uv run ty check
```

Verify all checks without changing files:

```sh
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Run every pre-commit check manually:

```sh
uv run pre-commit run --all-files
```

## Local Kafka

Kafka uses `confluentinc/cp-kafka:7.8.0` in single-node KRaft mode (no
ZooKeeper). Docker Compose v2 is required. Copy `.env.example` to `.env` and
fill in the existing application/DB settings before starting the full stack.
To start Kafka alone without a populated `.env`:

```sh
DB_PORT=5432 docker compose up -d --wait kafka
DB_PORT=5432 docker compose run --rm kafka-init
```

The healthcheck queries broker metadata. `kafka-init` waits for that healthcheck,
creates the topic if absent, and prints its actual partition description.
The host listener is `localhost:9092`, published only on loopback; Compose
containers use `kafka:29092`. The controller uses `kafka:29093` inside Docker.
These PLAINTEXT listeners are for local development.

| Setting | Default | Purpose |
| --- | --- | --- |
| `BATCH_BOOTSTRAP_SERVERS` | `localhost:9092` | Application bootstrap addresses (comma-separated); use `kafka:29092` inside Compose |
| `BATCH_TOPIC` | `activities.registration-requested` | Topic shared by publisher and consumer |
| `BATCH_GROUP_ID` | `mosemo.activity-batches` | Stable consumer group; retain across restarts |
| `BATCH_TOPIC_PARTITIONS` | `2` | Topic creation only; consumed by `kafka-init`, not application config |

The initializer uses `--create --if-not-exists` and never alters an existing
topic, even if `BATCH_TOPIC_PARTITIONS` changes. Changing partition count requires
a separate, deliberate migration because account key assignment and ordering can
change. Inspect the broker's actual value with:

```sh
DB_PORT=5432 docker compose exec kafka kafka-topics --bootstrap-server kafka:29092 --describe --topic activities.registration-requested
```

Messages, metadata, and offsets persist in the Compose `kafka-data` volume.
Replication factor is 1 for the local broker and its internal topics. This does
not provide replicated durability. Broker retention and production deployment
remain infrastructure concerns. Environment settings change addresses and names;
the approved message contract remains the same in local and production environments.
Application publisher/consumer wiring is tracked separately in #64 and #65.

Stop Kafka while retaining data:

```sh
DB_PORT=5432 docker compose stop kafka
```

For a deliberate local Kafka reset, remove only its container and named volume
(the Compose project name prefixes the volume; find the exact name first):

```sh
DB_PORT=5432 docker compose rm -sf kafka kafka-init
docker volume ls --filter label=com.docker.compose.volume=kafka-data
docker volume rm <project>_kafka-data
```

### Real broker tests

```sh
uv sync --locked
uv run --locked pytest -q tests/unit/test_config.py tests/integration/test_kafka.py
```

Docker must be running. Testcontainers starts the same pinned Kafka image with
a random host port, waits for broker startup, and removes the container at session
end. The async `integration_kafka` fixture creates a unique topic and group per
test, checks both partition leaders, and deletes the group and topic on teardown.
Clients must stop before fixture teardown; the smoke test demonstrates this with
`AsyncExitStack`. No local development topic/group is reused and Docker failures
fail the tests rather than switching to an in-memory broker.

The smoke test confirms ACKed byte/key delivery on both partitions and committed
group offsets. A second test reruns the actual initializer with partition count 3
and verifies that the existing topic still has 2. These establish the broker test
foundation; application ordering, parallel processing, separate processes, and
shutdown behavior belong to the subsequent implementation issues.

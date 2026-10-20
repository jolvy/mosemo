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
The API publishes using `acks=all` and an idempotent producer. It returns 202
after Kafka confirms publication, with a five second publication deadline and
the existing ten second overall HTTP admission deadline. A 503 with
`Retry-After: 1` means retry the same batch ID and content; a timeout can still
leave a published message. The HTTP body limit remains 512 KiB.

The API opens and closes its producer through the FastAPI lifespan in every
environment. `BATCH_ENABLED=false` explicitly disables publication (503);
there is no in-memory fallback. The Kafka value contains `accountId`, `batchId`,
`deviceId`, and `records`; the key is the authenticated account UUID encoded as
UTF-8. No account header or original HTTP byte wrapper is used.

Run the API and activity Consumer in separate terminals/processes with the same
DB, topic and group settings:

```sh
uv run --locked poe dev
uv run --locked poe batch-consumer
```

The Consumer uses a registered FastStream KafkaBroker subscriber with two native
manual-ACK workers; no application-owned Kafka consumer or polling loop is used.
It validates the message and matching account key, creates a fresh DB
session per attempt. BatchProcessingExecutor owns its transaction; the shared
ActivityBatchProcessor writes raw records, timeline and receipt within it.
Kafka processing takes no account advisory lock: serialization requires the same
topic, consumer group and account-key mapping, with sequential partition execution.
Each native worker waits for the registered handler, DB transaction and FastStream
message ACK before reading its next message. The two initial partitions run
concurrently; no unprocessed position is committed.
A stable group resumes valid committed offsets; otherwise it starts at the
earliest retained record. DB commit and offset commit are separate: receipt
idempotency protects a replay after a crash between them.

The direct `/api/v1/activities/batches/legacy` route uses LegacyBatchExecutor,
which owns a transaction and the account advisory lock (3-second timeout).
It returns success only after commit. Never use Kafka and legacy concurrently
for the same account; finish that account's work on the old path before switching.
Changing consumer groups, key mapping or partition count requires revisiting
account serialization. Deploy or roll back Processor and Executors together.

SIGINT/SIGTERM stops new batch execution and allows only in-flight batches up to
30 seconds for DB and offset commit, then cancels them and closes resources.
It does not drain the backlog. Reassignment cancels and cleans up old partition
executions before ownership transfers; unfinished offsets remain replayable.
The 30-second Consumer deadline is independent of Uvicorn's API shutdown.

Failure handling and recovery are unimplemented object boundaries. An unhandled
validation, processing or commit error is surfaced to the process boundary with
a nonzero exit and a payload-free diagnostic; it never reports processing success
or commits the failed record. This is exception propagation, not an approved
failure storage, blocking, retry or recovery policy. Those remain follow-up work;
this Consumer is not a claim of production readiness for failure handling.
The `/activities/batches/legacy` route continues to return 200 after direct DB
processing.

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
uv run --locked pytest -q tests/unit/test_config.py tests/integration/test_kafka.py tests/integration/test_batch_publication.py tests/integration/test_batch_consumption.py
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
and verifies that the existing topic still has 2. The Consumer tests additionally
verify partition sequencing and parallelism,
valid-offset resume and new-group earliest, two-member reassignment, shutdown,
and separate API/Consumer processes including a crash in the DB/offset gap.

The process test first publishes while no Consumer is running and checks that
202 leaves both receipts and raw records absent. It then starts a Consumer,
kills it after DB commit but before offset commit, and starts the production
Consumer entrypoint with the same group. The replay preserves the receipt,
raw record count and timeline segment IDs; a subsequent HTTP batch advances
the offset and SIGTERM exits cleanly.

The approved verification scope in [Plan #61](https://github.com/jolvy/mosemo/issues/61)
maps to the following tests. Execution results and the tested revision/diff are
recorded in [#66](https://github.com/jolvy/mosemo/issues/66), not inferred from this list.

| Scenario | Test location and boundary |
| --- | --- |
| TC-1 Message contract | `tests/unit/activities/test_delivery.py`, `tests/component/test_batch_consumer.py`, `tests/integration/test_batch_publication.py`: authenticated account key/value, schema and order |
| TC-2 HTTP acceptance | `tests/integration/test_batch_publication.py`, `tests/component/test_batch_runtime.py`, `tests/component/test_batch_admission_timeout.py`: real ACK, 503 and admission deadlines |
| TC-3 Atomic persistence | `tests/integration/test_activity_batches.py`: PostgreSQL receipt, raw records and timeline commit/rollback |
| TC-4 Duplicate/conflict | `tests/integration/test_activity_batches.py`: normalized retry and changed content preserve existing results |
| TC-5 Ordering/concurrency | `tests/integration/test_batch_consumption.py`: real partition assignment, DB/offset sequencing and parallel execution |
| TC-6 Separate processes | `tests/integration/test_batch_consumption.py`: HTTP API and Consumer OS processes with real Kafka/PostgreSQL |
| TC-7 Resume/shutdown | `tests/integration/test_batch_consumption.py`, `tests/component/test_batch_consumer.py`: committed offsets, earliest and bounded in-flight drain |
| TC-8 Initial schema | `tests/integration/test_migrations.py::test_fresh_receipt_schema_has_no_source`: empty PostgreSQL migration and receipt columns |
| TC-9 Commit gap replay | `tests/integration/test_batch_consumption.py`: process kill between DB and offset commits, replay and subsequent progress |

Run all nine scopes and the existing business regressions with the repository
quality gate (Docker must be running):

```sh
bash .codex/skills/verification/scripts/validate.sh
```

DB feature regressions in `tests/batch_support.py` use the direct `/batches/legacy`
route; they verify processing rules, not Kafka delivery. The real broker tests
above exercise the delivery path. Failure storage, blocking, retry exhaustion
and manual recovery remain outside this verification scope and unimplemented;
passing this gate does not establish production readiness.

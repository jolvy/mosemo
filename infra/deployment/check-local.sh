#!/usr/bin/env bash
# Isolated smoke check; uses only disposable test credentials and local Docker.
set -euo pipefail
cd "$(dirname "$0")/../.."
image=mosemo-server:deployment-check
task_tmp=$(mktemp -d)
task_id="mosemo-check-$(basename "$task_tmp" | tr '[:upper:].' '[:lower:]-')"
cleanup() {
  docker rm -fv "$task_id-api" "$task_id-consumer" "$task_id-kafka" "$task_id-db" >/dev/null 2>&1 || true
  docker network rm "$task_id" >/dev/null 2>&1 || true
  rm -rf "$task_tmp"
}
trap cleanup EXIT
cat > "$task_tmp/test.env" <<'ENV'
MOSEMO_ENV=test
BATCH_ENABLED=true
BATCH_BOOTSTRAP_SERVERS=kafka:29092
BATCH_TOPIC=activities.registration-requested
BATCH_GROUP_ID=mosemo.activity-batches
DB_HOST=db
DB_PORT=5432
DB_DATABASE=mosemo_test
DB_USER=mosemo
DB_PASSWORD=local-test-password
KAKAO_REST_API_KEY=test-rest-api-key
KAKAO_CLIENT_SECRET=test-client-secret
KAKAO_REDIRECT_URI=http://localhost:8000/api/v1/auth/kakao/callback
AUTH_JWT_SECRET_KEY=test-jwt-secret-key-that-is-at-least-32-bytes
AUTH_MACOS_CALLBACK_URI=io.mosemo.app:/auth/callback
PGSSLMODE=disable
ENV

docker buildx build --platform linux/amd64 --load -t "$image" .
test "$(docker image inspect "$image" --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
docker network create "$task_id" >/dev/null
docker run -d --name "$task_id-db" --network "$task_id" --network-alias db \
  -e POSTGRES_DB=mosemo_test -e POSTGRES_USER=mosemo \
  -e POSTGRES_PASSWORD=local-test-password postgres:18.6 >/dev/null
ready=false
for ((i=0; i<60; i++)); do
  if docker exec "$task_id-db" pg_isready -U mosemo -d mosemo_test >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  docker logs "$task_id-db"
  exit 1
fi
docker run -d --platform linux/amd64 --memory 768m --memory-swap 768m --name "$task_id-kafka" \
  --network "$task_id" --network-alias kafka \
  -e CLUSTER_ID=MkU3OEVBNTcwNTJENDM2Qk -e KAFKA_NODE_ID=1 \
  -e KAFKA_PROCESS_ROLES=broker,controller \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@kafka:29093 \
  -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
  -e KAFKA_LISTENERS=INTERNAL://0.0.0.0:29092,CONTROLLER://0.0.0.0:29093 \
  -e KAFKA_ADVERTISED_LISTENERS=INTERNAL://kafka:29092 \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=INTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=INTERNAL \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 \
  -e KAFKA_AUTO_CREATE_TOPICS_ENABLE=false \
  -e KAFKA_LOG_CLEANER_DEDUPE_BUFFER_SIZE=33554432 \
  -e KAFKA_HEAP_OPTS='-Xms128m -Xmx256m' \
  confluentinc/cp-kafka:7.8.0 >/dev/null
ready=false
for ((i=0; i<60; i++)); do
  if docker exec -e KAFKA_HEAP_OPTS='-Xms32m -Xmx64m' "$task_id-kafka" kafka-topics --bootstrap-server kafka:29092 --list >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 2
done
if [[ "$ready" != true ]]; then
  docker logs "$task_id-kafka"
  exit 1
fi
docker exec -e KAFKA_HEAP_OPTS='-Xms32m -Xmx64m' "$task_id-kafka" kafka-topics --bootstrap-server kafka:29092 \
  --create --if-not-exists --topic activities.registration-requested \
  --partitions 2 --replication-factor 1
for ((i=0; i<2; i++)); do
  docker run --rm --platform linux/amd64 --network "$task_id" \
    --env-file "$task_tmp/test.env" "$image" alembic upgrade head
done
docker run --rm --platform linux/amd64 --network "$task_id" \
  --env-file "$task_tmp/test.env" "$image" alembic current
# Exercise the application's own engine, including a migrated table.
docker run --rm --platform linux/amd64 --network "$task_id" \
  --env-file "$task_tmp/test.env" "$image" python -c '
import asyncio
from sqlalchemy import text
from mosemo.platform.database.engine import get_engine
async def check():
    engine = get_engine()
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM accounts")) == 0
        print("Application DB query: OK")
    await engine.dispose()
asyncio.run(check())'
docker run -d --platform linux/amd64 --memory 512m --memory-swap 512m --name "$task_id-consumer" --network "$task_id" \
  --env-file "$task_tmp/test.env" "$image" python -m mosemo.entrypoints.consumer >/dev/null
ready=false
for ((i=0; i<30; i++)); do
  if docker exec -e KAFKA_HEAP_OPTS='-Xms32m -Xmx64m' "$task_id-kafka" kafka-consumer-groups --bootstrap-server kafka:29092 \
    --describe --group mosemo.activity-batches --state 2>/dev/null | grep -q Stable; then
    ready=true
    break
  fi
  sleep 2
done
if [[ "$ready" != true ]]; then
  docker logs "$task_id-consumer"
  exit 1
fi
docker run -d --platform linux/amd64 --memory 512m --memory-swap 512m --name "$task_id-api" --network "$task_id" \
  -p 127.0.0.1::8000 --env-file "$task_tmp/test.env" "$image" >/dev/null
api_port=$(docker port "$task_id-api" 8000/tcp | cut -d: -f2)
ready=false
for ((i=0; i<60; i++)); do
  if curl --fail --silent "http://127.0.0.1:$api_port/openapi.json" > "$task_tmp/openapi.json"; then
    ready=true
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  docker logs "$task_id-api"
  exit 1
fi
# Parse the actual response inside the same image, using its installed Python.
docker exec -i "$task_id-api" python -c \
  'import json,sys; doc=json.load(sys.stdin); assert doc["info"]["title"] == "Mosemo API"; assert "/api/v1/accounts/me" in doc["paths"]; print("OpenAPI HTTP response: OK")' \
  < "$task_tmp/openapi.json"
test "$(curl --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:$api_port/api/v1/accounts/me")" = 401
test "$(docker exec "$task_id-api" id -u)" = 10001
docker stop --time 15 "$task_id-api" >/dev/null
test "$(docker inspect "$task_id-api" --format '{{.State.ExitCode}}')" = 0
docker stop --time 45 "$task_id-consumer" >/dev/null
test "$(docker inspect "$task_id-consumer" --format '{{.State.ExitCode}}')" = 0
printf '%s\n' 'PASS: linux/amd64 build, migration + rerun, DB query, real Kafka, separate consumer group Stable, HTTP, non-root, graceful shutdown.'
printf '%s\n' 'Not checked: Kafka-to-DB message flow in this smoke check (covered by integration tests), ECR, EKS, RDS TLS, ALB, real OAuth.'

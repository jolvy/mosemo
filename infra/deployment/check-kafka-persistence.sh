#!/usr/bin/env bash
# Test broker replacement using a disposable local volume, never AWS or real data.
set -euo pipefail
cd "$(dirname "$0")/../.."
image=mosemo-server:deployment-check
docker image inspect "$image" >/dev/null
task_tmp=$(mktemp -d)
task_id="mosemo-persist-$(basename "$task_tmp" | tr '[:upper:].' '[:lower:]-')"
cleanup() {
  docker rm -fv "$task_id-kafka" >/dev/null 2>&1 || true
  docker volume rm "$task_id-data" >/dev/null 2>&1 || true
  docker network rm "$task_id" >/dev/null 2>&1 || true
  rm -rf "$task_tmp"
}
trap cleanup EXIT
docker network create "$task_id" >/dev/null
docker volume create "$task_id-data" >/dev/null
start_broker() {
  docker run -d --platform linux/amd64 --memory 768m --memory-swap 768m \
    --user 1000:1000 --name "$task_id-kafka" --network "$task_id" --network-alias kafka \
    --mount "type=volume,src=$task_id-data,dst=/var/lib/kafka/data" \
    -e CLUSTER_ID=MkU3OEVBNTcwNTJENDM2Qk -e KAFKA_NODE_ID=1 \
    -e KAFKA_PROCESS_ROLES=broker,controller \
    -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@kafka:9093 \
    -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
    -e KAFKA_LISTENERS=INTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093 \
    -e KAFKA_ADVERTISED_LISTENERS=INTERNAL://kafka:9092 \
    -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=INTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT \
    -e KAFKA_INTER_BROKER_LISTENER_NAME=INTERNAL \
    -e KAFKA_LOG_DIRS=/var/lib/kafka/data/kafka \
    -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
    -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
    -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 \
    -e KAFKA_AUTO_CREATE_TOPICS_ENABLE=false \
    -e KAFKA_LOG_CLEANER_DEDUPE_BUFFER_SIZE=33554432 \
    -e KAFKA_HEAP_OPTS='-Xms128m -Xmx256m' confluentinc/cp-kafka:7.8.0 >/dev/null
  for ((i=0; i<60; i++)); do
    if docker exec -e KAFKA_HEAP_OPTS='-Xms32m -Xmx64m' "$task_id-kafka" \
      kafka-topics --bootstrap-server kafka:9092 --list >/dev/null 2>&1; then
      return
    fi
    sleep 2
  done
  docker logs "$task_id-kafka"
  return 1
}
start_broker
docker exec -e KAFKA_HEAP_OPTS='-Xms32m -Xmx64m' "$task_id-kafka" \
  kafka-topics --bootstrap-server kafka:9092 --create \
  --topic persistence-check --partitions 1 --replication-factor 1
docker run --rm -i --platform linux/amd64 --network "$task_id" "$image" python - <<'PY'
import asyncio
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.structs import TopicPartition

async def check():
    producer = AIOKafkaProducer(bootstrap_servers="kafka:9092", acks="all")
    await producer.start()
    try:
        await producer.send_and_wait("persistence-check", b"committed")
        await producer.send_and_wait("persistence-check", b"pending")
    finally:
        await producer.stop()
    consumer = AIOKafkaConsumer(
        "persistence-check", bootstrap_servers="kafka:9092",
        group_id="persistence-check", enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        message = await asyncio.wait_for(consumer.getone(), 30)
        assert message.value == b"committed" and message.offset == 0
        await consumer.commit({TopicPartition(message.topic, message.partition): 1})
    finally:
        await consumer.stop()

asyncio.run(check())
PY
docker stop --time 60 "$task_id-kafka" >/dev/null
docker rm "$task_id-kafka" >/dev/null
start_broker
docker run --rm -i --platform linux/amd64 --network "$task_id" "$image" python - <<'PY'
import asyncio
from aiokafka import AIOKafkaConsumer

async def check():
    consumer = AIOKafkaConsumer(
        "persistence-check", bootstrap_servers="kafka:9092",
        group_id="persistence-check", enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        message = await asyncio.wait_for(consumer.getone(), 30)
        assert message.value == b"pending" and message.offset == 1
    finally:
        await consumer.stop()

asyncio.run(check())
PY
printf '%s\n' 'PASS: broker container replaced; topic, pending message and committed group offset preserved.'
printf '%s\n' 'Not checked: AWS EBS/CSI mount, Kubernetes rescheduling, abrupt crash, disk loss or backup.'

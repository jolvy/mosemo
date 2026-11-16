#!/usr/bin/env bash
# 측정 A 절차 로컬 드라이런(5단계 local-all 방식). 랩 대신 Mac 일회용 컨테이너로 같은 순서를 끝까지 돈다.
#   랩 흉내: m7a-rds(postgres:18.3, pg_stat_statements 미리 올림) + 빈 스키마 마이그레이션(랩 migration Job 대역)
#            m7a-kafka(cp-kafka 7.8.0, 토픽 파티션 8) + 로컬 API(uvicorn)·consumer 1개(이 저장소 코드, batch 프로세스 없음)
#   측정 쪽: run-load-measurement.sh LOCAL=1 check → prep(a0 관문·a1 복원·a2 재배치·VACUUM) → consumer 시작 → mixed(짧게)
#            → final → post-dump → fetch → fetch-dump
# 끝나면(실패해도) m7a-rds·m7a-kafka 컨테이너·볼륨과 로컬 프로세스를 지운다. 다른 컨테이너는 건드리지 않는다.
#
#   사용: local-dryrun.sh            환경: LOCAL_CONSUMERS(1), DUMP(기본 ~/mosemo-step5/smoke-7a.dump), MIXED_SEC(60), MIXED_WARM(10), MIXED_GRACE(15)
#   결과: ~/mosemo-step5/dryrun-7a-<UTC>/ (state/에 회수 묶음·로그, dryrun.log)
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(cd "$ART/../.." && pwd)}"
PY="$REPO/.venv/bin/python"
DUMP="${DUMP:-$HOME/mosemo-step5/smoke-7a.dump}"
ALEMBIC_FILE="${DUMP%.dump}.alembic.txt"
RUN="$HOME/mosemo-step5/dryrun-7a-$(date -u +%Y%m%dT%H%M%SZ)"
RDS_PG=m7a-rds; RDS_PORT=55474
KAFKA=m7a-kafka; KAFKA_PORT=59092
API_PORT=8774
K6_BIN="$HOME/mosemo-step5/k6/v2.3.0/k6-v2.3.0-macos-arm64/k6"
mkdir -p "$RUN/work" "$RUN/state"
LOG="$RUN/dryrun.log"
log() { echo "[$(date -u +%FT%TZ)] dryrun: $*" | tee -a "$LOG"; }
API_PID=""; CON_PIDS=()
LOCAL_CONSUMERS="${LOCAL_CONSUMERS:-1}"   # 로컬 consumer 프로세스 수(랩은 4)
teardown() {
  local p; for p in ${CON_PIDS[@]+"${CON_PIDS[@]}"}; do pkill -P "$p" 2>/dev/null || true; kill "$p" 2>/dev/null || true; done
  [[ -n "$API_PID" ]] && { pkill -P "$API_PID" 2>/dev/null || true; kill "$API_PID" 2>/dev/null || true; }
  docker rm -fv "$RDS_PG" "$KAFKA" >/dev/null 2>&1 || true
  log "정리: 로컬 API·consumer 종료, ${RDS_PG}·$KAFKA 컨테이너·볼륨 삭제"
}
trap teardown EXIT

for c in "$RDS_PG" "$KAFKA"; do docker ps -a --format '{{.Names}}' | grep -qx "$c" && { echo "${c}가 이미 있다" >&2; exit 2; }; done
[[ -f "$DUMP" && -f "$ALEMBIC_FILE" ]] || { echo "덤프·head 파일 없음: $DUMP" >&2; exit 2; }
log "repo $REPO commit $(git -C "$REPO" rev-parse HEAD), 덤프 $DUMP sha256 $(shasum -a 256 "$DUMP" | awk '{print $1}')"

ENV_FILE="$RUN/m7a.env"
cat > "$ENV_FILE" <<EOF
MOSEMO_ENV=local
DB_DATABASE=mosemo
DB_USER=mosemo
DB_PASSWORD=m7a-$(openssl rand -hex 12)
DB_HOST=127.0.0.1
DB_PORT=$RDS_PORT
BATCH_ENABLED=true
BATCH_BOOTSTRAP_SERVERS=127.0.0.1:$KAFKA_PORT
BATCH_TOPIC=activities.registration-requested
BATCH_GROUP_ID=mosemo.activity-batches
KAKAO_REST_API_KEY=fake
KAKAO_CLIENT_SECRET=fake
KAKAO_REDIRECT_URI=http://localhost:8000/api/v1/auth/kakao/callback
AUTH_JWT_SECRET_KEY=m7a-jwt-$(openssl rand -hex 24)
AUTH_JWT_ISSUER=mosemo
AUTH_JWT_AUDIENCE=mosemo-api
AUTH_ACCESS_TOKEN_TTL_SECONDS=86400
AUTH_AUTHORIZATION_CODE_TTL_SECONDS=60
AUTH_MACOS_CALLBACK_URI=com.example.mosemo:/auth/callback
OPENAI_API_KEY=fake
GOOGLE_API_KEY=fake
LABEL_MODEL=openai:fake-model
EOF
chmod 600 "$ENV_FILE"
set -a; source "$ENV_FILE"; set +a

log "랩 대역: $RDS_PG(postgres:18.3, pg_stat_statements·track_io_timing), $KAFKA(파티션 8)"
docker run -d --name "$RDS_PG" -e POSTGRES_DB=mosemo -e POSTGRES_USER=mosemo -e POSTGRES_PASSWORD="$DB_PASSWORD" \
  -p 127.0.0.1:$RDS_PORT:5432 --memory=2g --cpus=2 --shm-size=256m postgres:18.3 \
  -c shared_buffers=512MB -c max_connections=200 -c shared_preload_libraries=pg_stat_statements -c track_io_timing=on >/dev/null
docker run -d --name "$KAFKA" --hostname "$KAFKA" -p 127.0.0.1:$KAFKA_PORT:9092 \
  -e CLUSTER_ID=MkU3OEVBNTcwNTJENDM2Qk -e KAFKA_NODE_ID=1 -e KAFKA_PROCESS_ROLES=broker,controller \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:29093 -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
  -e KAFKA_LISTENERS=INTERNAL://0.0.0.0:29092,EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:29093 \
  -e KAFKA_ADVERTISED_LISTENERS=INTERNAL://localhost:29092,EXTERNAL://127.0.0.1:$KAFKA_PORT \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=INTERNAL -e KAFKA_AUTO_CREATE_TOPICS_ENABLE=false \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 -e KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0 \
  -e KAFKA_HEAP_OPTS="-Xms256m -Xmx512m" confluentinc/cp-kafka:7.8.0 >/dev/null
until docker exec "$RDS_PG" pg_isready -U mosemo -d mosemo >/dev/null 2>&1; do sleep 1; done; sleep 2
(cd "$REPO" && uv run --locked --env-file "$ENV_FILE" alembic upgrade head) >> "$LOG" 2>&1
log "빈 스키마 마이그레이션(랩 migration Job 대역) → $(docker exec "$RDS_PG" psql -XAt -U mosemo -d mosemo -c 'SELECT version_num FROM alembic_version')"
for _ in $(seq 60); do docker exec "$KAFKA" kafka-topics --bootstrap-server localhost:29092 --list >/dev/null 2>&1 && break; sleep 2; done
docker exec "$KAFKA" kafka-topics --bootstrap-server localhost:29092 --create --if-not-exists \
  --topic "$BATCH_TOPIC" --partitions 8 --replication-factor 1 >> "$LOG" 2>&1
log "토픽 $(docker exec "$KAFKA" kafka-topics --bootstrap-server localhost:29092 --describe --topic "$BATCH_TOPIC" | sed -n 's/.*PartitionCount: *\([0-9]*\).*/파티션 \1/p' | head -1)"

(cd "$REPO" && exec "$PY" -m uvicorn mosemo.entrypoints.api.main:app --host 127.0.0.1 --port $API_PORT --log-level warning) > "$RUN/api.log" 2>&1 &
API_PID=$!
for _ in $(seq 60); do curl -s -o /dev/null "http://127.0.0.1:$API_PORT/openapi.json" && break; sleep 1; done
curl -sf -o /dev/null "http://127.0.0.1:$API_PORT/openapi.json" || { log "API가 뜨지 않음(api.log)"; exit 1; }
log "로컬 API $API_PORT"

export LOCAL=1 W="$RUN/work" PY ENV_FILE K6_BIN API_URL="http://127.0.0.1:$API_PORT" STATE="$RUN/state" \
       DUMP ALEMBIC_FILE RDS_PG S5_REPO="$REPO" \
       MIXED_SEC="${MIXED_SEC:-60}" MIXED_WARM="${MIXED_WARM:-10}" MIXED_GRACE="${MIXED_GRACE:-15}"
S="$ART/run-load-measurement.sh"
"$S" prep
for i in $(seq 1 "$LOCAL_CONSUMERS"); do
  (cd "$REPO" && exec "$PY" -m mosemo.entrypoints.consumer) > "$RUN/consumer-$i.log" 2>&1 &
  CON_PIDS+=($!)
done
sleep 8
for p in "${CON_PIDS[@]}"; do kill -0 "$p" 2>/dev/null || { log "consumer가 죽었다(consumer-*.log)"; exit 1; }; done
log "로컬 consumer ${LOCAL_CONSUMERS}개 시작(복원·재배치 뒤 = 랩의 consumer 0 → 4 순서 대역)"
"$S" mixed
"$S" final
"$S" post-dump
"$S" fetch
"$S" fetch-dump
log "드라이런 끝. 결과 $RUN"

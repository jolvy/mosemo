#!/usr/bin/env bash
# 측정 A 절차 로컬 드라이런(5단계 local-all 방식). 랩 대신 Mac 일회용 컨테이너로 같은 순서를 끝까지 돈다.
#   랩 흉내: m7a-rds(PostgreSQL 18.3, pg_stat_statements 미리 올림) + 빈 스키마 마이그레이션(랩 migration Job 대역)
#            m7a-kafka(cp-kafka 7.8.0, 토픽 파티션 8) + 로컬 API(uvicorn)·consumer 1개(이 저장소 코드, batch 프로세스 없음)
#   측정 쪽: run-load-measurement.sh LOCAL=1 check → prep(a0 관문·a1 복원·a2 재배치·VACUUM) → consumer 시작 → mixed(짧게)
#            → final → post-dump → fetch → fetch-dump
# 끝나면(실패해도) 이 실행이 만든 m7a-rds·m7a-kafka 컨테이너·볼륨과 로컬 프로세스를 지운다. 다른 컨테이너는 건드리지 않는다
# (이름이 이미 있으면 아무것도 만들거나 지우지 않고 멈춘다).
#
#   사용: local-dryrun.sh            환경: LOCAL_CONSUMERS(1), DUMP(기본 ~/mosemo-step5/smoke-7a.dump), MIXED_SEC(60), MIXED_WARM(10), MIXED_GRACE(15)
#   MEASUREMENT=delete-scale: 덤프 대신 7B 계정 삭제 측정(run-delete-scale-measurement.sh all, LOCAL=1)을 끝까지 돈다.
#         consumer를 먼저 띄우고 USERS(20)·DAYS(3)·MEASURE_DATE(오늘)를 넘긴다. 덤프·head 파일 검사와 prep·post-dump는 하지 않는다.
#   MEASUREMENT=b: 측정 B(measurement-b.md)를 run-load-measurement.sh all(LOCAL=1)로 6회차 끝까지 돈다. 회차 시간은 위 MIXED_*,
#         되돌리기 상한 RESET_LIMIT_S(120). consumer는 run-load-measurement.sh가 되돌리기 전후에 LOCAL_CONSUMER_STOP·START로
#         멈추고 다시 띄운다(로컬 consumer는 DB 연결이 끊기면 종료한다). 조회 로그는 API 로그(api.log)에서 모은다.
#   결과: ~/mosemo-step5/dryrun-7a-<UTC>/ (state/에 회수 묶음·로그, dryrun.log)
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(cd "$ART/../.." && pwd)}"
PY="$REPO/.venv/bin/python"
MEASUREMENT="${MEASUREMENT:-}"
RUN="$HOME/mosemo-step5/dryrun-7a-$(date -u +%Y%m%dT%H%M%SZ)"
if [[ "$MEASUREMENT" == delete-scale ]]; then
  DUMP=none; ALEMBIC_FILE="$RUN/state/alembic-head.txt"   # db-measurement-steps.sh local이 값을 요구한다(읽지 않음)
else
  DUMP="${DUMP:-$HOME/mosemo-step5/smoke-7a.dump}"
  ALEMBIC_FILE="${DUMP%.dump}.alembic.txt"
fi
RDS_PG=m7a-rds; RDS_PORT=55474
KAFKA=m7a-kafka; KAFKA_PORT=59092
API_PORT=8774
K6_BIN="$HOME/mosemo-step5/k6/v2.3.0/k6-v2.3.0-macos-arm64/k6"
LOG="$RUN/dryrun.log"
log() { echo "[$(date -u +%FT%TZ)] dryrun: $*" | tee -a "$LOG"; }
API_PID=""; CON_PIDS=(); CREATED=()
LOCAL_CONSUMERS="${LOCAL_CONSUMERS:-1}"   # 로컬 consumer 프로세스 수(랩은 4)
teardown() {  # 이 실행이 만든 컨테이너(CREATED)만 지운다
  local p; for p in ${CON_PIDS[@]+"${CON_PIDS[@]}"}; do pkill -P "$p" 2>/dev/null || true; kill "$p" 2>/dev/null || true; done
  [[ -x "$RUN/consumer-stop.sh" ]] && "$RUN/consumer-stop.sh"
  [[ -n "$API_PID" ]] && { pkill -P "$API_PID" 2>/dev/null || true; kill "$API_PID" 2>/dev/null || true; }
  (( ${#CREATED[@]} )) && { docker rm -fv "${CREATED[@]}" >/dev/null 2>&1 || true; }
  log "정리: 로컬 API·consumer 종료, 만든 컨테이너·볼륨 삭제(${CREATED[*]:-없음})"
}
# 컨테이너는 create가 성공한 것만 CREATED에 넣고 start한다. 이름 충돌(다른 세션)은 create에서 실패해 지울 목록에 들어가지 않는다
up() {  # up <이름> <docker create 인자...>
  docker create --name "$1" "${@:2}" >/dev/null
  CREATED+=("$1")
  docker start "$1" >/dev/null
}

# 검사(멈추면 아무것도 만들거나 지우지 않는다) → 결과 폴더 → 정리 trap
for c in "$RDS_PG" "$KAFKA"; do docker ps -a --format '{{.Names}}' | grep -qx "$c" && { echo "${c}가 이미 있다" >&2; exit 2; }; done
if [[ "$MEASUREMENT" != delete-scale ]]; then
  [[ -f "$DUMP" && -f "$ALEMBIC_FILE" ]] || { echo "덤프·head 파일 없음: $DUMP" >&2; exit 2; }
fi
mkdir -p "$RUN/work" "$RUN/state"
trap teardown EXIT
if [[ "$MEASUREMENT" == delete-scale ]]; then
  log "repo $REPO commit $(git -C "$REPO" rev-parse HEAD), 7B 계정 삭제 측정(덤프 없음)"
else
  log "repo $REPO commit $(git -C "$REPO" rev-parse HEAD), 덤프 $DUMP sha256 $(shasum -a 256 "$DUMP" | awk '{print $1}')"
fi

ENV_FILE="$RUN/m7a.env"
(umask 077   # env 파일(비밀번호·JWT 키)을 처음부터 소유자만 읽게 쓴다. 다른 결과 파일 권한은 바꾸지 않는다
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
)
chmod 600 "$ENV_FILE"
set -a; source "$ENV_FILE"; set +a

log "랩 대역: $RDS_PG(PostgreSQL 18.3, pg_stat_statements·track_io_timing), $KAFKA(파티션 8)"
POSTGRES_PASSWORD="$DB_PASSWORD" up "$RDS_PG" -e POSTGRES_DB=mosemo -e POSTGRES_USER=mosemo -e POSTGRES_PASSWORD \
  -p 127.0.0.1:$RDS_PORT:5432 --memory=2g --cpus=2 --shm-size=256m postgres:18.3@sha256:7e32e9833a6fb1c92c32552794cb6ed569d51b445a54907d35fc112ef39684db \
  -c shared_buffers=512MB -c max_connections=200 -c shared_preload_libraries=pg_stat_statements -c track_io_timing=on
up "$KAFKA" --hostname "$KAFKA" -p 127.0.0.1:$KAFKA_PORT:9092 \
  -e CLUSTER_ID=MkU3OEVBNTcwNTJENDM2Qk -e KAFKA_NODE_ID=1 -e KAFKA_PROCESS_ROLES=broker,controller \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:29093 -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
  -e KAFKA_LISTENERS=INTERNAL://0.0.0.0:29092,EXTERNAL://0.0.0.0:9092,CONTROLLER://0.0.0.0:29093 \
  -e KAFKA_ADVERTISED_LISTENERS=INTERNAL://localhost:29092,EXTERNAL://127.0.0.1:$KAFKA_PORT \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=INTERNAL -e KAFKA_AUTO_CREATE_TOPICS_ENABLE=false \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 -e KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0 \
  -e KAFKA_HEAP_OPTS="-Xms256m -Xmx512m" confluentinc/cp-kafka:7.8.0@sha256:adc392d28a1e99e8c9a1ec7f087e9e91041837b35b8b7cc8b8a691b82dd581b0
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
start_consumers() {
  local i p
  for i in $(seq 1 "$LOCAL_CONSUMERS"); do
    (cd "$REPO" && exec "$PY" -m mosemo.entrypoints.consumer) > "$RUN/consumer-$i.log" 2>&1 &
    CON_PIDS+=($!)
  done
  sleep 8
  for p in "${CON_PIDS[@]}"; do kill -0 "$p" 2>/dev/null || { log "consumer가 죽었다(consumer-*.log)"; exit 1; }; done
  log "로컬 consumer ${LOCAL_CONSUMERS}개 시작(복원·재배치 뒤 = 랩의 consumer 0 → 4 순서 대역)"
}
if [[ "$MEASUREMENT" == b ]]; then
  # 측정 B: consumer는 run-load-measurement.sh가 회차마다 멈추고 띄운다. PID는 consumers.pid에 둔다
  cat > "$RUN/consumer-start.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
for i in \$(seq 1 $LOCAL_CONSUMERS); do
  (cd "$REPO" && set -a && source "$ENV_FILE" && set +a && exec "$PY" -m mosemo.entrypoints.consumer) >> "$RUN/consumer-\$i.log" 2>&1 &
  echo \$! >> "$RUN/consumers.pid"
done
sleep 8
for p in \$(cat "$RUN/consumers.pid"); do kill -0 "\$p" 2>/dev/null || { echo "consumer가 죽었다(consumer-*.log)" >&2; exit 1; }; done
EOF
  cat > "$RUN/consumer-stop.sh" <<EOF
#!/usr/bin/env bash
[[ -f "$RUN/consumers.pid" ]] || exit 0
for p in \$(cat "$RUN/consumers.pid"); do pkill -P "\$p" 2>/dev/null; kill "\$p" 2>/dev/null; done
for p in \$(cat "$RUN/consumers.pid"); do while kill -0 "\$p" 2>/dev/null; do sleep 1; done; done
rm -f "$RUN/consumers.pid"
EOF
  chmod +x "$RUN/consumer-start.sh" "$RUN/consumer-stop.sh"
  MEASUREMENT=b API_LOG="$RUN/api.log" RESET_LIMIT_S="${RESET_LIMIT_S:-120}" \
    LOCAL_CONSUMER_START="$RUN/consumer-start.sh" LOCAL_CONSUMER_STOP="$RUN/consumer-stop.sh" "$S" all
  log "드라이런 끝(측정 B). 결과 $RUN"
  exit 0
fi
if [[ "$MEASUREMENT" == delete-scale ]]; then
  start_consumers
  USERS="${USERS:-20}" DAYS="${DAYS:-3}" MEASURE_DATE="${MEASURE_DATE:-$(date +%F)}" "$ART/run-delete-scale-measurement.sh" all
  log "드라이런 끝(7B 계정 삭제 측정). 결과 $RUN"
  exit 0
fi
"$S" prep
start_consumers
"$S" mixed
"$S" final
"$S" post-dump
"$S" fetch
"$S" fetch-dump
log "드라이런 끝. 결과 $RUN"

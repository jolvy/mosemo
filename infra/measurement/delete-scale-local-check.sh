#!/usr/bin/env bash
# 7B 측정 생성기 로컬 검사. 일회용 PostgreSQL 18.3(Pod pg 컨테이너와 같은 digest)을 띄우고 head 마이그레이션 → prepare →
# 생성(기본 50명 × 2일) → verify(행 수·연결 위반·흩어짐) → 문맥 8개를 앱 모델(ActivityContext)로 검증한다.
# 끝나면(실패해도) 컨테이너·볼륨을 지운다. 다른 컨테이너는 건드리지 않는다.
#
#   사용: delete-scale-local-check.sh      환경: USERS(50), DAYS(2), FIRST(2026-08-01), PORT(55476), REPO(이 저장소 루트)
#   흩어짐 0.95 기준은 한 페이지 행 수(약 16)보다 사용자가 넉넉히 많아야 성립한다. USERS를 20 아래로 낮추지 않는다.
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(cd "$ART/../.." && pwd)}"
USERS="${USERS:-50}"
DAYS="${DAYS:-2}"
FIRST="${FIRST:-2026-08-01}"
PORT="${PORT:-55476}"
C=x7b-check
PW="check-$(openssl rand -hex 8)"
WORK=$(mktemp -d)
log() { echo "[$(date -u +%FT%TZ)] local-check: $*" >&2; }
teardown() { docker rm -fv "$C" > /dev/null 2>&1 || true; rm -rf "$WORK"; }
trap teardown EXIT

docker ps -a --format '{{.Names}}' | grep -qx "$C" && { echo "${C}가 이미 있다" >&2; exit 2; }
IMAGE=postgres:18.3@sha256:7e32e9833a6fb1c92c32552794cb6ed569d51b445a54907d35fc112ef39684db
POSTGRES_PASSWORD="$PW" docker run -d --name "$C" -e POSTGRES_DB=mosemo -e POSTGRES_PASSWORD -p "127.0.0.1:$PORT:5432" \
  --shm-size=256m "$IMAGE" -c shared_buffers=512MB -c max_wal_size=1GB > /dev/null
until docker exec "$C" pg_isready -U postgres -d mosemo > /dev/null 2>&1; do sleep 1; done; sleep 2
(cd "$REPO" && DB_HOST=127.0.0.1 DB_PORT="$PORT" DB_DATABASE=mosemo DB_USER=postgres DB_PASSWORD="$PW" \
  uv run --locked alembic upgrade head) > "$WORK/migrate.log" 2>&1 || { cat "$WORK/migrate.log" >&2; exit 1; }
log "head $(docker exec "$C" psql -XAt -U postgres -d mosemo -c 'SELECT version_num FROM alembic_version')"

S() {  # pg 쪽 명령을 Pod의 pg 컨테이너처럼 별도 PostgreSQL 18.3 클라이언트 컨테이너에서
  PGPASSWORD="$PW" docker run --rm -i --network "container:$C" -v "$ART:/x7b:ro" -v "$WORK:/work" \
    -e PGHOST=127.0.0.1 -e PGPORT=5432 -e PGUSER=postgres -e PGPASSWORD -e PGDATABASE=mosemo -e W=/work \
    "$IMAGE" bash /x7b/delete-scale-steps.sh "$@"
}
S prepare
S generate "$FIRST" "$USERS" "$DAYS"
S verify
S contexts > "$WORK/contexts.jsonl"
(cd "$REPO" && uv run --locked python -c '
import json, sys
from pydantic import TypeAdapter
from mosemo.observation.presentation.activities.schemas import ActivityContext
adapter = TypeAdapter(ActivityContext)
lines = [line for line in open(sys.argv[1]) if line.strip()]
for line in lines:
    adapter.validate_python(json.loads(line))
assert len(lines) == 8, len(lines)
print(f"contexts ok: {len(lines)}")
' "$WORK/contexts.jsonl")
log "통과: ${USERS}명 × ${DAYS}일"

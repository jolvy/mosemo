#!/usr/bin/env bash
# 측정 A(7단계 뒤) 덤프 변환. 5단계 덤프(alembic head a3b4c5d6e7f8)를 일회용 로컬 PG(postgres:18.3, RDS와 같은 판)에
# 복원하고, 이 저장소 코드로 alembic upgrade head(c5d6e7f8a9b0)를 한 뒤 새 덤프를 만든다.
# 원래 덤프는 읽기만 한다. 변환 전후 모든 기존 테이블의 행 수(count(*))가 같아야 하고, 새 테이블은 정의 행 1개 외에 비어 있어야 한다.
#
#   사용: convert-seed-dump.sh <원래 덤프> <새 덤프> <이름 꼬리>      예) convert-seed-dump.sh ~/mosemo-step5/smoke.dump ~/mosemo-step5/smoke-7a.dump smoke
#   환경: REPO(기본 이 파일이 있는 저장소 루트), PORT(기본 55471), EXPECT_FROM(a3b4c5d6e7f8), EXPECT_TO(c5d6e7f8a9b0)
#   산출: <새 덤프>, <새 덤프>.sha256, <새 덤프 이름>.alembic.txt, <새 덤프 이름>.counts.txt(변환 전·후 행 수와 판정), 로그 <새 덤프 이름>.convert.log
#   컨테이너 m7a-conv-<이름 꼬리>는 끝나면(실패해도) 볼륨까지 지운다. 다른 컨테이너는 건드리지 않는다.
set -euo pipefail

SRC=$1; DST=$2; TAG=$3
ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${REPO:-$(cd "$ART/../.." && pwd)}"
PORT="${PORT:-55471}"
EXPECT_FROM="${EXPECT_FROM:-a3b4c5d6e7f8}"
EXPECT_TO="${EXPECT_TO:-c5d6e7f8a9b0}"
PG_IMAGE=postgres:18.3
NAME="m7a-conv-$TAG"
BASE="${DST%.dump}"
LOG="$BASE.convert.log"
COUNTS="$BASE.counts.txt"
ENVF="$(mktemp -t m7a-env)"

[[ -f "$SRC" ]] || { echo "원래 덤프 없음: $SRC" >&2; exit 2; }
[[ "$SRC" != "$DST" ]] || { echo "원래 덤프를 덮어쓰지 않는다" >&2; exit 2; }
[[ ! -e "$DST" ]] || { echo "새 덤프가 이미 있다: $DST (지우고 다시)" >&2; exit 2; }
docker ps -a --format '{{.Names}}' | grep -qx "$NAME" && { echo "$NAME 컨테이너가 이미 있다" >&2; exit 2; }

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
cleanup() { docker rm -fv "$NAME" >/dev/null 2>&1 || true; rm -f "$ENVF"; }
trap cleanup EXIT
PW="m7a-$(openssl rand -hex 12)"
Q() { docker exec "$NAME" psql -XAt -U mosemo -d mosemo -v ON_ERROR_STOP=1 -c "$1"; }
counts() {  # 모든 public 기본 테이블의 정확한 행 수(이름순)
  local t
  for t in $(Q "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"); do
    echo "$t $(Q "SELECT count(*) FROM public.\"$t\"")"
  done
}

: > "$LOG"
log "원래 덤프 $SRC sha256 $(shasum -a 256 "$SRC" | awk '{print $1}')"
log "repo $REPO commit $(git -C "$REPO" rev-parse HEAD) dirty=$(git -C "$REPO" status --porcelain | wc -l | tr -d ' ')"
# 일회용 변환용이라 fsync·full_page_writes를 끈다(복원 속도. 덤프 내용에는 영향 없음)
docker run -d --name "$NAME" -e POSTGRES_DB=mosemo -e POSTGRES_USER=mosemo -e POSTGRES_PASSWORD="$PW" \
  -p 127.0.0.1:"$PORT":5432 --shm-size=512m "$PG_IMAGE" \
  -c shared_buffers=1GB -c max_wal_size=4GB -c fsync=off -c full_page_writes=off -c synchronous_commit=off >/dev/null
until docker exec "$NAME" pg_isready -U mosemo -d mosemo >/dev/null 2>&1; do sleep 1; done; sleep 2
log "$NAME 시작($(Q 'SELECT version()' | cut -c1-40))"

start=$(date +%s)
docker cp "$SRC" "$NAME:/tmp/src.dump"
docker exec "$NAME" pg_restore -U mosemo -d mosemo --no-owner --no-privileges -j 4 --exit-on-error /tmp/src.dump
docker exec "$NAME" rm -f /tmp/src.dump
log "복원 $(( $(date +%s) - start ))s, DB $(Q "SELECT pg_size_pretty(pg_database_size('mosemo'))")"
head_from=$(Q "SELECT version_num FROM alembic_version")
[[ "$head_from" == "$EXPECT_FROM" ]] || { log "멈춤: 복원 뒤 head $head_from ≠ $EXPECT_FROM"; exit 3; }
counts > "$BASE.counts-before.tmp"
log "변환 전 head $head_from, 테이블 $(wc -l < "$BASE.counts-before.tmp" | tr -d ' ')개"

start=$(date +%s)
printf 'DB_HOST=127.0.0.1\nDB_PORT=%s\nDB_DATABASE=mosemo\nDB_USER=mosemo\nDB_PASSWORD=%s\n' "$PORT" "$PW" > "$ENVF"
(cd "$REPO" && uv run --locked --env-file "$ENVF" alembic upgrade head) >> "$LOG" 2>&1
head_to=$(Q "SELECT version_num FROM alembic_version")
[[ "$head_to" == "$EXPECT_TO" ]] || { log "멈춤: upgrade 뒤 head $head_to ≠ $EXPECT_TO"; exit 4; }
log "alembic upgrade head $(( $(date +%s) - start ))s → $head_to"
counts > "$BASE.counts-after.tmp"

# 판정: 기존 테이블 행 수 같음(alembic_version 포함 1행), 새 테이블은 analytics_definitions 1행 외 0행, pauses_reported는 모두 false
python3 - "$BASE.counts-before.tmp" "$BASE.counts-after.tmp" > "$COUNTS" <<'PY'
import sys
b = dict(l.split() for l in open(sys.argv[1]) if l.strip())
a = dict(l.split() for l in open(sys.argv[2]) if l.strip())
ok = True
print("table before after verdict")
for t in sorted(set(a) | set(b)):
    x, y = b.get(t), a.get(t)
    if t in b:
        v = "same" if x == y else "DIFF"
    else:  # 7단계 새 테이블
        want = "1" if t == "analytics_definitions" else "0"
        v = "new-ok" if y == want else f"new-BAD(want {want})"
    ok &= v in ("same", "new-ok")
    if t not in a:
        v, ok = "MISSING", False
    print(t, x or "-", y or "-", v)
print("RESULT", "PASS" if ok else "FAIL")
PY
rm -f "$BASE.counts-before.tmp" "$BASE.counts-after.tmp"
extra=$(Q "SELECT count(*) FILTER (WHERE pauses_reported) || ' ' || count(*) FROM focus_sessions")
echo "focus_sessions pauses_reported_true/total $extra" >> "$COUNTS"
defs=$(Q "SELECT string_agg(id || ':' || version, ',') FROM analytics_definitions")
echo "analytics_definitions id:version $defs" >> "$COUNTS"
[[ "$defs" == "1:1" ]] || { log "멈춤: analytics_definitions가 1:1이 아니다($defs)"; exit 5; }
echo "sequence analytics_daily_marker_version_seq is_called=$(Q 'SELECT is_called FROM analytics_daily_marker_version_seq')" >> "$COUNTS"
grep -q '^RESULT PASS' "$COUNTS" || { log "멈춤: 행 수 판정 실패($COUNTS)"; cat "$COUNTS"; exit 5; }
[[ "${extra%% *}" == 0 ]] || { log "멈춤: pauses_reported true 행이 있다"; exit 5; }
log "행 수 판정 PASS($COUNTS)"

start=$(date +%s)
docker exec "$NAME" pg_dump -U mosemo -d mosemo -Fc -Z 6 --no-owner --no-privileges -f /tmp/dst.dump
docker cp "$NAME:/tmp/dst.dump" "$DST" >/dev/null
echo "$head_to" > "$BASE.alembic.txt"
shasum -a 256 "$DST" > "$DST.sha256"
log "새 덤프 $DST $(wc -c < "$DST" | tr -d ' ') B, $(( $(date +%s) - start ))s, sha256 $(awk '{print $1}' "$DST.sha256")"
log "끝(컨테이너 $NAME 삭제)"

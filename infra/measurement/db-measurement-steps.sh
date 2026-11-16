#!/usr/bin/env bash
# 측정 A(7단계 뒤 기준 사양 재측정) DB 쪽 절차. STEP7A-PREREG.md 「데이터 준비」·「질의별 통계」를 그대로 실행한다.
# 쿼리는 하네스(db_harness.py sql)로만 보낸다
# (앱 이미지에 psql 없음). 복원·덤프만 pg 컨테이너(PostgreSQL 18.3)에서 pg_restore·pg_dump로 한다.
#
#   사용: db-measurement-steps.sh <단계> [이름]
#     a0            관문(복원 전): pg_stat_statements 사용 가능·초기화, track_io_timing 기록, DB가 빈 스키마(계정·레코드 0),
#                   7단계 테이블(정의 행 1:1, 표시 0, 일간 행 0), head 셋(앱 이미지·DB·덤프) = EXPECT_HEAD        (tools)
#     a1            pg_restore --clean --if-exists -j 2 → ANALYZE                                              (pg 컨테이너)
#     a2            관문(복원 뒤) → 시간순 재배치(reorder-segments-by-time.sql) → VACUUM (ANALYZE) → inventory.json → 상태 기록 (tools)
#     rec <이름>    질의별 통계·DB 통계·표시 수 기록. 이름이 before-로 시작하면 기록 뒤 pg_stat_statements 초기화        (tools)
#     final         측정 끝 상태: 일간 행 0(batch가 돌지 않았음), 표시 수·날짜 분포, 테이블 행 수·크기                    (tools)
#     post-dump     측정 직후 DB 덤프(pg_dump -Fc -Z 6) → $W/post-7a.dump(.sha256)                            (pg 컨테이너)
#   측정 B(measurement-b.md, 관리용 DB postgres에 붙어 트랜잭션 밖에서 실행하는 SQL은 S5_SQL_TIMEOUT = RESET_LIMIT_S):
#     b-perm              DB를 만들 권한(rolcreatedb·슈퍼유저·rds_superuser)과 작업 DB 소유자 확인. 아니면 멈춘다       (tools)
#     b-seed <제외 계정>   구간 많은 계정(dense_day_accounts.py) → 2026-11-12 표시(시드 계정, 제외·구간 많은 계정 빼고)
#                         → VACUUM (ANALYZE)                                                                          (tools)
#     b-base              작업 DB를 원본 DB(BASE_DB)로 복사: 새 연결 막기 → 연결 끊기 → FILE_COPY → 다시 열기       (tools)
#     b-reset <n> <남은 묶음>  작업 DB를 지우고(WITH FORCE) 원본에서 다시 만듦 → 통계 초기화 → b-reset.txt 한 줄    (tools)
#     b-round-start <n> <제외 계정>  회차 시작 표시 수·시드 계정 수 → b-round-start.json                       (tools)
#     b-final             일간 행·표시·크기 → b-final.txt, 원본 DB 삭제                                         (tools)
#     b-restore           작업 DB가 없고 원본이 있으면 원본에서 다시 만든다(중간에 멈췄을 때)                       (tools)
#   대상: TARGET=eks(측정 Pod 안, 기본) | local(Mac 드라이런: m7a-rds 컨테이너, local-dryrun.sh가 부른다)
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${TARGET:-eks}"
EXPECT_HEAD="${EXPECT_HEAD:-c5d6e7f8a9b0}"
END_DATE="${END_DATE:-2026-11-12}"     # 시드 마지막 날 = 조회 부하의 "오늘"(5단계와 같음)
PG_IMAGE=postgres:18.3@sha256:7e32e9833a6fb1c92c32552794cb6ed569d51b445a54907d35fc112ef39684db
if [[ "$TARGET" == eks ]]; then
  W="${W:-/work}"
  REPO="${S5_REPO:-$W/repo}"
  PY="${PY:-python}"
  DUMP="${DUMP:-$W/seed.dump}"                  # measurement-pod.sh push가 DUMP를 이 이름으로 넣는다
  ALEMBIC_FILE="${ALEMBIC_FILE:-$W/s1-alembic.txt}"
else
  W="${W:?local은 W(작업 폴더)를 준다}"
  REPO="${S5_REPO:-$(cd "$ART/../.." && pwd)}"
  PY="${PY:-$REPO/.venv/bin/python}"
  DUMP="${DUMP:?local은 DUMP를 준다}"
  ALEMBIC_FILE="${ALEMBIC_FILE:?local은 ALEMBIC_FILE을 준다}"
  RDS_PG="${RDS_PG:-m7a-rds}"
  ENV_FILE="${ENV_FILE:?local은 ENV_FILE을 준다}"
  set -a; source "$ENV_FILE"; set +a   # DB_*·AUTH_JWT_*(하네스·준비 스크립트가 읽음)
fi
OUT="$W/results"
WORK_DB="${DB_DATABASE:-mosemo}"
BASE_DB="${BASE_DB:-mosemo_b_base}"
MARKER_DATE="${MARKER_DATE:-2026-11-12}"
RESET_LIMIT_S="${RESET_LIMIT_S:-600}"
mkdir -p "$OUT"
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT/a-run.log"; }
die() { log "멈춤: $*"; exit 1; }
H() { S5_REPO="$REPO" "$PY" "$ART/db_harness.py" "$@"; }
SQLQ() { H sql --query "$1"; }
VAL() { SQLQ "$1" | sed -n 2p; }       # 첫 행 첫 열(머리줄 다음 줄)

table_counts() {  # 모든 public 기본 테이블 행 수와 크기
  local t
  for t in $(SQLQ "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename" | sed '1d;/^-- /d'); do
    echo "$t $(VAL "SELECT count(*) FROM public.\"$t\"") $(VAL "SELECT pg_total_relation_size('public.\"$t\"')")"
  done
}
image_head() {
  if [[ "$TARGET" == eks ]]; then (cd /app && alembic heads 2>/dev/null | awk 'NF {print $1; exit}')
  else (cd "$REPO" && "$PY" -m alembic heads 2>/dev/null | awk 'NF {print $1; exit}'); fi
}
heads_check() {
  local img db dump
  img=$(image_head); db=$(VAL "SELECT version_num FROM alembic_version"); dump=$(cat "$ALEMBIC_FILE" 2>/dev/null || echo "?")
  echo "heads image=$img db=$db dump=$dump expect=$EXPECT_HEAD" | tee -a "$OUT/a-heads.txt"
  [[ "$img" == "$EXPECT_HEAD" && "$db" == "$EXPECT_HEAD" && "$dump" == "$EXPECT_HEAD" ]] || die "alembic head 불일치($img / $db / $dump, 기대 $EXPECT_HEAD)"
}
analytics_check() {  # $1 = 이름. 정의 행 1:1, 일간 행 0(batch가 돌지 않음). 표시 수는 기록만
  local defs stats markers
  defs=$(VAL "SELECT coalesce(string_agg(id || ':' || version, ','), '') FROM analytics_definitions")
  stats=$(VAL "SELECT count(*) FROM analytics_daily_stats")
  markers=$(VAL "SELECT count(*) FROM analytics_daily_markers")
  echo "$1 definitions=$defs daily_stats=$stats markers=$markers" | tee -a "$OUT/a-analytics.txt"
  [[ "$defs" == "1:1" ]] || die "analytics_definitions가 1:1이 아니다($defs)"
  [[ "$stats" == 0 ]] || die "analytics_daily_stats가 ${stats}행(batch가 돌았거나 남은 데이터)"
}

a0() {
  log "A0 관문(복원 전, TARGET=$TARGET)"
  {
    SQLQ "SELECT version()"
    SQLQ "SELECT name, setting, unit FROM pg_settings WHERE name IN ('shared_buffers','effective_cache_size','work_mem',
          'maintenance_work_mem','max_connections','shared_preload_libraries','track_io_timing','random_page_cost',
          'io_method','effective_io_concurrency','max_wal_size','default_transaction_isolation','pg_stat_statements.max',
          'pg_stat_statements.track') ORDER BY name"
    SQLQ "SELECT current_database(), pg_size_pretty(pg_database_size(current_database())) AS size"
  } > "$OUT/a0-check.txt" 2>&1
  grep -q "^shared_preload_libraries | .*pg_stat_statements" "$OUT/a0-check.txt" \
    || die "shared_preload_libraries에 pg_stat_statements가 없다(파라미터 그룹 결정 필요)"
  SQLQ "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" > /dev/null
  SQLQ "SELECT pg_stat_statements_reset()" > /dev/null
  if grep -q "^track_io_timing | on" "$OUT/a0-check.txt"; then log "A0 track_io_timing on"
  else log "A0 주의: track_io_timing off → 블록 읽기 시간 열이 0(보고에 적는다)"; fi
  local n; n=$(VAL "SELECT (SELECT count(*) FROM accounts) + (SELECT count(*) FROM activity_records)")
  [[ "$n" == 0 ]] || die "복원 전 DB에 데이터가 있다(계정+레코드 ${n}행). 측정 A는 빈 스키마에 새로 복원한 상태에서만 시작한다"
  analytics_check a0
  heads_check
  log "A0 통과"
}

a1() {
  log "A1 pg_restore 시작($DUMP)"
  local start; start=$(date +%s)
  if [[ "$TARGET" == eks ]]; then   # pg 컨테이너(PGHOST 등은 Pod 환경)
    pg_restore --clean --if-exists --no-owner --no-privileges -j 2 -v -d "$PGDATABASE" "$DUMP" > "$OUT/a1-restore.log" 2>&1 \
      || die "pg_restore 오류(a1-restore.log)"
    psql -qX -c "ANALYZE"
    echo "memory_peak_bytes=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null)" >> "$OUT/a1-restore.log"
  else   # Pod의 pg 컨테이너처럼 별도 클라이언트 컨테이너(768Mi)에서
    PGPASSWORD="$DB_PASSWORD" docker run --rm --memory=768m --network "container:$RDS_PG" -v "$(dirname "$DUMP")":/w -e PGPASSWORD \
      "$PG_IMAGE" sh -c "pg_restore -h 127.0.0.1 -U $DB_USER -d $DB_DATABASE --clean --if-exists --no-owner --no-privileges -j 2 -v \
        /w/$(basename "$DUMP") && psql -qX -h 127.0.0.1 -U $DB_USER -d $DB_DATABASE -c ANALYZE; rc=\$?; \
        echo memory_peak_bytes=\$(cat /sys/fs/cgroup/memory.peak 2>/dev/null); exit \$rc" \
      > "$OUT/a1-restore.log" 2>&1 || die "pg_restore 오류(a1-restore.log)"
  fi
  log "A1 복원 $(( $(date +%s) - start ))s"
}

a2() {
  export S5_SQL_TIMEOUT=7200   # 재배치·VACUUM(약 12GB)은 길다
  log "A2 관문(복원 뒤)"
  local n; n=$(VAL "SELECT count(*) FROM accounts")
  [[ "$n" -gt 0 ]] || die "복원 뒤 계정 0"
  analytics_check a2-after-restore
  [[ "$(VAL "SELECT count(*) FROM analytics_daily_markers")" == 0 ]] || die "복원 직후 표시가 있다"
  heads_check
  local start; start=$(date +%s)
  log "A2 시간순 재배치(reorder-segments-by-time.sql: 구간·확정 테이블, 5단계와 같은 파일)"
  H sql --file "$ART/reorder-segments-by-time.sql" > "$OUT/a2-reorder.txt" || die "재배치 실패(a2-reorder.txt)"
  grep -A2 '겹침 검사' "$OUT/a2-reorder.txt" | grep -qE '^0$' || log "A2 주의: 겹침 검사 결과 확인 필요(a2-reorder.txt)"
  log "A2 VACUUM (ANALYZE) 전체"
  SQLQ "VACUUM (ANALYZE)" > /dev/null
  log "A2 재배치·VACUUM $(( $(date +%s) - start ))s"
  H inventory --out "$OUT/inventory.json" --today "$END_DATE"
  {
    echo "# a2 상태 $(date -u +%FT%TZ)"
    SQLQ "SELECT pg_database_size(current_database()) AS db_bytes"
    table_counts
  } > "$OUT/a2-state.txt"
  SQLQ "SELECT pg_stat_reset()" > /dev/null          # DB 통계(pg_stat_database·user_tables)는 측정 직전부터
  SQLQ "SELECT pg_stat_statements_reset()" > /dev/null
  log "A2 끝(inventory.json, a2-state.txt). 다음: consumer 켜기 → 1회차"
}

rec() {
  local name=${1:?rec <이름>}
  {
    echo "# $name $(date -u +%FT%TZ)"
    echo "## pg_stat_statements 상위 50(총 실행 시간 순)"
    SQLQ "SELECT queryid, calls, round(total_exec_time::numeric,1) total_ms, round(mean_exec_time::numeric,3) mean_ms,
            rows, shared_blks_hit hit, shared_blks_read rd, shared_blks_dirtied dirtied, shared_blks_written written,
            round(shared_blk_read_time::numeric,1) rd_ms, wal_bytes,
            left(regexp_replace(query, '\s+', ' ', 'g'), 300) q
          FROM pg_stat_statements WHERE calls > 0 ORDER BY total_exec_time DESC LIMIT 50"
    echo "## pg_stat_statements 상위 30(버퍼 적중 순)"
    SQLQ "SELECT queryid, calls, shared_blks_hit hit, round(shared_blks_hit::numeric / greatest(calls,1),1) hit_per_call,
            left(regexp_replace(query, '\s+', ' ', 'g'), 300) q
          FROM pg_stat_statements WHERE calls > 0 ORDER BY shared_blks_hit DESC LIMIT 30"
    echo "## pg_stat_statements 합계"
    SQLQ "SELECT count(*) statements, sum(calls) calls, round(sum(total_exec_time)::numeric,1) total_ms,
            sum(shared_blks_hit) hit, sum(shared_blks_read) rd FROM pg_stat_statements"
    echo "## pg_stat_database"
    SQLQ "SELECT now(), xact_commit, xact_rollback, numbackends, tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted,
            blks_hit, blks_read, temp_bytes FROM pg_stat_database WHERE datname = current_database()"
    echo "## pg_stat_user_tables(쓰기·스캔 상위 15)"
    SQLQ "SELECT relname, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch, n_tup_ins, n_tup_upd, n_tup_hot_upd, n_tup_del, n_live_tup, n_dead_tup
          FROM pg_stat_user_tables ORDER BY coalesce(n_tup_ins,0) + coalesce(n_tup_upd,0) + coalesce(seq_tup_read,0) DESC LIMIT 15"
    echo "## 표시(analytics_daily_markers)"
    SQLQ "SELECT kind, count(*) n, count(DISTINCT account_id) accounts, min(local_date) min_d, max(local_date) max_d,
            count(*) FILTER (WHERE eligible_at <= now()) eligible_now FROM analytics_daily_markers GROUP BY kind"
    SQLQ "SELECT (SELECT count(*) FROM analytics_daily_stats) daily_stats, (SELECT count(*) FROM activity_batch_receipts) receipts"
  } > "$OUT/a-$name.txt" 2>&1 || log "rec $name 일부 실패(a-$name.txt)"
  if [[ "$name" == before-* ]]; then
    SQLQ "SELECT pg_stat_statements_reset()" > /dev/null
    echo "pgss_reset $(date -u +%FT%TZ)" >> "$OUT/a-$name.txt"
  fi
  log "rec $name → a-$name.txt"
}

final() {
  analytics_check final
  {
    echo "# final $(date -u +%FT%TZ)"
    SQLQ "SELECT local_date, kind, count(*) n, count(*) FILTER (WHERE eligible_at <= now()) eligible_now
          FROM analytics_daily_markers GROUP BY 1, 2 ORDER BY 1, 2"
    SQLQ "SELECT pg_database_size(current_database()) AS db_bytes"
    table_counts
  } > "$OUT/a-final.txt"
  log "final: 일간 행 0 확인(batch 미실행), 표시 분포·행 수 a-final.txt"
}

post_dump() {
  local start; start=$(date +%s)
  log "post-dump 시작(측정 직후 DB, D-51 교훈)"
  if [[ "$TARGET" == eks ]]; then
    pg_dump -Fc -Z 6 --no-owner --no-privileges -d "$PGDATABASE" -f "$W/post-7a.dump"
  else
    docker exec "$RDS_PG" pg_dump -U "$DB_USER" -d "$DB_DATABASE" -Fc -Z 6 --no-owner --no-privileges -f /tmp/post-7a.dump
    docker cp "$RDS_PG:/tmp/post-7a.dump" "$W/post-7a.dump" >/dev/null
    docker exec "$RDS_PG" rm -f /tmp/post-7a.dump
  fi
  (cd "$W" && sha256sum post-7a.dump 2>/dev/null || shasum -a 256 post-7a.dump) > "$W/post-7a.dump.sha256"
  log "post-dump $(wc -c < "$W/post-7a.dump" | tr -d ' ') B, $(( $(date +%s) - start ))s, $(awk '{print $1}' "$W/post-7a.dump.sha256")"
}


# ---------------------------------------------------------------- 측정 B
ident() { [[ "$1" =~ ^[a-z_][a-z0-9_]*$ ]] || die "DB 이름이 식별자가 아니다: $1"; }
uuids() { [[ -z "$1" || "$1" =~ ^[0-9a-f-]{36}(,[0-9a-f-]{36})*$ ]] || die "계정 ID 목록 형식: $1"; }
quoted() { local x out=""; for x in ${1//,/ }; do out+="${out:+,}'$x'"; done; echo "${out:-'00000000-0000-0000-0000-000000000000'}"; }
ADM() { DB_DATABASE=postgres S5_SQL_TIMEOUT="$RESET_LIMIT_S" H sql --query "$1"; }   # 관리용 DB, 문장 하나(트랜잭션 밖)
ADMVAL() { ADM "$1" | sed -n 2p; }
dense_ids() { "$PY" -c 'import json,sys;print(",".join(json.load(open(sys.argv[1])).values()))' "$OUT/b-dense.json"; }
db_exists() { [[ "$(ADMVAL "SELECT count(*) FROM pg_database WHERE datname = '$1'")" == 1 ]]; }

b_perm() {
  ident "$WORK_DB"; ident "$BASE_DB"
  local can owner
  can=$(VAL "SELECT r.rolcreatedb OR r.rolsuper OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
             WHERE m.member = r.oid AND g.rolname = 'rds_superuser') FROM pg_roles r WHERE r.rolname = current_user")
  owner=$(VAL "SELECT pg_get_userbyid(datdba) = current_user FROM pg_database WHERE datname = current_database()")
  echo "b-perm createdb=$can owner=$owner db=$WORK_DB base=$BASE_DB" | tee -a "$OUT/a-run.log"
  [[ "$can" == True && "$owner" == True ]] || die "DB를 만들 권한이 없거나 작업 DB 소유자가 아니다(createdb=$can owner=$owner)"
}

b_seed() {
  local excl=${1:-} dense n
  uuids "$excl"
  log "B 구간 많은 계정(dense_day_accounts.py)"
  "$PY" "$ART/dense_day_accounts.py" --out-dir "$OUT" || die "구간 많은 계정 생성 실패(b-dense.txt)"
  dense=$(dense_ids)
  SQLQ "INSERT INTO analytics_daily_markers (account_id, local_date, version, kind, marked_at, eligible_at, attempts, next_attempt_at)
        SELECT a.id, DATE '$MARKER_DATE', nextval('analytics_daily_marker_version_seq'), 'source', now(),
               (DATE '$MARKER_DATE' + 1)::timestamp AT TIME ZONE 'Asia/Seoul' + interval '1 hour', 0, now()
        FROM accounts a WHERE a.id NOT IN ($(quoted "$excl,$dense"))
        ON CONFLICT (account_id, local_date) DO UPDATE SET version = nextval('analytics_daily_marker_version_seq'),
          kind = 'source', marked_at = excluded.marked_at, eligible_at = excluded.eligible_at, attempts = 0,
          next_attempt_at = excluded.next_attempt_at, last_error = NULL, last_failed_at = NULL" > "$OUT/b-seed-markers.txt"
  n=$(VAL "SELECT count(*) FROM analytics_daily_markers WHERE local_date = DATE '$MARKER_DATE'")
  log "B $MARKER_DATE 표시 ${n}개"
  S5_SQL_TIMEOUT=7200 SQLQ "VACUUM (ANALYZE)" > /dev/null
}

b_base() {
  ident "$WORK_DB"; ident "$BASE_DB"
  local start; start=$(date +%s)
  db_exists "$BASE_DB" && die "원본 DB $BASE_DB가 이미 있다(앞선 측정의 잔존물). 확인하고 지운 뒤 다시"
  ADM "ALTER DATABASE $WORK_DB ALLOW_CONNECTIONS false" > /dev/null
  ADM "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = '$WORK_DB' AND pid <> pg_backend_pid()" > /dev/null
  if ! ADM "CREATE DATABASE $BASE_DB TEMPLATE $WORK_DB STRATEGY FILE_COPY" > "$OUT/b-base.txt" 2>&1; then
    ADM "ALTER DATABASE $WORK_DB ALLOW_CONNECTIONS true" > /dev/null
    die "원본 DB 복사 실패(b-base.txt)"
  fi
  ADM "ALTER DATABASE $WORK_DB ALLOW_CONNECTIONS true" > /dev/null
  ADM "ALTER DATABASE $BASE_DB ALLOW_CONNECTIONS false" > /dev/null
  echo "base_copy_s=$(( $(date +%s) - start ))" >> "$OUT/b-base.txt"
  log "B 원본 DB $BASE_DB 복사 $(( $(date +%s) - start ))s"
}

b_reset() {
  local n=${1:?b-reset <n> <남은 묶음>} left=${2:-0} s0 s1 e0
  ident "$WORK_DB"; ident "$BASE_DB"
  s0=$(date -u +%FT%TZ); e0=$(date +%s)
  ADM "DROP DATABASE IF EXISTS $WORK_DB WITH (FORCE)" > /dev/null || die "작업 DB 지우기 실패"
  ADM "CREATE DATABASE $WORK_DB TEMPLATE $BASE_DB STRATEGY FILE_COPY" > /dev/null || die "작업 DB 다시 만들기 실패"
  SQLQ "SELECT pg_stat_statements_reset()" > /dev/null
  SQLQ "SELECT pg_stat_reset()" > /dev/null
  s1=$(date -u +%FT%TZ)
  echo "reset r$n $s0 $s1 $(( $(date +%s) - e0 )) leftover=$left" >> "$OUT/b-reset.txt"
  log "B 되돌리기 r$n $(( $(date +%s) - e0 ))s(앞 회차 남은 묶음 $left)"
}

b_round_start() {
  local n=${1:?b-round-start <n> <제외 계정>} excl=${2:-} dense markers seeds
  uuids "$excl"
  dense=$(dense_ids)
  markers=$(VAL "SELECT count(*) FROM analytics_daily_markers WHERE local_date = DATE '$MARKER_DATE'")
  seeds=$(VAL "SELECT count(*) FROM accounts WHERE id NOT IN ($(quoted "$excl,$dense"))")
  "$PY" - "$OUT/b-round-start.json" "$n" "$markers" "$seeds" <<'EOF'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
rows = json.loads(p.read_text()) if p.exists() else []
rows.append({"round": int(sys.argv[2]), "markers_2026_11_12": int(sys.argv[3]), "seed_accounts": int(sys.argv[4])})
p.write_text(json.dumps(rows, indent=1) + "\n")
EOF
  log "B 회차 r$n 시작: $MARKER_DATE 표시 $markers, 시드 계정 $seeds"
}

b_final() {
  {
    echo "# b-final $(date -u +%FT%TZ)"
    SQLQ "SELECT (SELECT count(*) FROM analytics_daily_stats) daily_stats, (SELECT count(*) FROM analytics_daily_markers) markers,
            (SELECT count(*) FROM analytics_daily_markers WHERE local_date = DATE '$MARKER_DATE') markers_marker_date"
    SQLQ "SELECT local_date, kind, count(*) n FROM analytics_daily_markers GROUP BY 1, 2 ORDER BY 1, 2"
    SQLQ "SELECT pg_database_size(current_database()) AS db_bytes"
    table_counts
  } > "$OUT/b-final.txt"
  ident "$BASE_DB"
  ADM "DROP DATABASE IF EXISTS $BASE_DB" >> "$OUT/b-final.txt"
  log "B final: b-final.txt, 원본 DB $BASE_DB 삭제"
}

b_restore() {
  ident "$WORK_DB"; ident "$BASE_DB"
  db_exists "$WORK_DB" && ADM "ALTER DATABASE $WORK_DB ALLOW_CONNECTIONS true" > /dev/null   # b-base 중간에 멈춘 경우
  if ! db_exists "$WORK_DB" && db_exists "$BASE_DB"; then
    ADM "CREATE DATABASE $WORK_DB TEMPLATE $BASE_DB STRATEGY FILE_COPY" > /dev/null && log "B 작업 DB를 원본에서 다시 만듦(중단 복구)"
  fi
}

case "${1:-}" in
  a0|a1|a2|final) "$1" ;;
  b-perm) b_perm ;;
  b-seed) b_seed "${2:-}" ;;
  b-base) b_base ;;
  b-reset) b_reset "${2:-}" "${3:-0}" ;;
  b-round-start) b_round_start "${2:-}" "${3:-}" ;;
  b-final) b_final ;;
  b-restore) b_restore ;;
  rec) rec "${2:-}" ;;
  post-dump) post_dump ;;
  *) sed -n '2,21p' "$0"; exit 2 ;;
esac

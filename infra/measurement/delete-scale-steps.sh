#!/usr/bin/env bash
# 7B 계정 삭제 운영 규모 측정의 DB 쪽 절차. 측정 Pod의 pg 컨테이너(psql)에서 PG* 환경 변수로 접속한다.
# 같은 폴더의 SQL 파일을 읽는다. 결과는 X7B_OUT(기본 $W/results/x7b)에 남긴다.
#
#   사용: delete-scale-steps.sh <명령> [인자]
#     prepare                         인덱스 후보·설정(delete-scale-prepare.sql) + 프로시저 3개 SQL 올리기
#     generate <첫날> <사용자> <일수>   생성. x7b_glog의 마지막 묶음 다음부터 잇는다(설정이 다르면 멈춤)
#     after-generate                  ANALYZE, 제약 유효성, 표별 행 수, 4개 표 VACUUM 1회, 통계 초기화
#     account <u>                     x7b_gu 사용자 번호의 계정 ID 출력
#     delete <이름> <one|day> <계정> [이관 날짜|-] [부하 표시]
#                                     계정 삭제(실제 커밋). 이관 날짜를 주면 같은 때 야간 이관 삭제를 돌리고,
#                                     이관이 끝난 직후와 삭제가 끝난 뒤 4개 표 VACUUM (VERBOSE)
#     hooks <D> <L1 계정> <L2 계정>     k6 회차 시작 파일($W/k6out/mixed-rN.start)에 맞춰 L1·L2 삭제
#     verify                          생성 결과 검사(행 수·연결 위반·흩어짐). 하나라도 어긋나면 종료 코드 1
#     contexts                        생성기 문맥 8개를 한 줄에 하나씩 JSON으로 출력
#     cleanup --yes                   실험 객체(인덱스 6개·설정·프로시저·x7b 표)와 생성 데이터를 지운다
#   환경: W(/work), X7B_OUT, MIXED_WARM(60), HOOK_GAP(30)
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
W="${W:-/work}"
OUT="${X7B_OUT:-$W/results/x7b}"
MIXED_WARM="${MIXED_WARM:-60}"
HOOK_GAP="${HOOK_GAP:-30}"
TABLES4="activity_records activity_timeline_segments activity_label_confirmations activity_label_proposals"

utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { mkdir -p "$OUT"; echo "[$(utc)] delete-scale: $*" | tee -a "$OUT/steps.log" >&2; }
die() { log "멈춤: $*"; exit 1; }
Q() { psql -X -v ON_ERROR_STOP=1 "$@"; }
V() { psql -X -v ON_ERROR_STOP=1 -At -c "$1"; }

prepare() {
  local f
  for f in delete-scale-prepare.sql account-delete-procedure.sql archive-delete-procedure.sql delete-scale-generate.sql; do
    Q -q -f "$ART/$f" 2>&1 | grep -v -e 'already exists, skipping' >&2 || true
  done
  log "prepare: 인덱스 후보 $(V "SELECT count(*) FROM pg_indexes WHERE indexname LIKE 'x7b\_%' AND indexname NOT LIKE '%\_pkey'")개, 프로시저 $(V "SELECT count(*) FROM pg_proc WHERE proname IN ('x7b_gen_setup','x7b_gen_day','x7b_account_delete','x7b_archive_delete')")개"
}

generate() {
  local first=${1:?첫날} users=${2:?사용자 수} days=${3:?일수} cfg last dn chunk s
  cfg=$(V "SELECT coalesce(string_agg(k || '=' || v, ',' ORDER BY k), '') FROM x7b_gconf WHERE k IN ('first', 'users')")
  if [[ -z "$cfg" ]]; then
    Q -q -c "CALL x7b_gen_setup($users, '$first')"
    log "generate: 설정 users=$users first=$first"
  elif [[ "$cfg" != "first=$first,users=$users" ]]; then
    die "이미 다른 설정으로 생성된 DB($cfg). cleanup 뒤 다시 한다"
  fi
  Q -q -c "INSERT INTO x7b_gconf VALUES ('days', '$days') ON CONFLICT (k) DO UPDATE SET v = excluded.v"
  # 마지막으로 끝난 묶음: 묶음 -1은 sessions, 0~13은 receipts 행이 마지막에 남는다(묶음과 같은 트랜잭션)
  last=$(V "SELECT coalesce(max(dn * 100 + chunk + 1), -1) FROM x7b_glog WHERE tbl IN ('sessions', 'receipts')")
  if (( last < 0 )); then dn=0; chunk=-1
  else dn=$(( last / 100 )); chunk=$(( last % 100 )); (( chunk > 13 )) && { dn=$((dn + 1)); chunk=-1; }; fi
  (( dn > 0 || chunk > -1 )) && log "generate: 날짜 $dn 묶음 $chunk부터 잇는다"
  for (( ; dn < days; dn++ )); do
    s=$(date +%s)
    Q -q -c "CALL x7b_gen_day($dn, $chunk)" 2>&1 | grep -v 'x7b_kr' >&2 || true
    [[ "$(V "SELECT count(*) FROM x7b_glog WHERE dn = $dn AND chunk = 13 AND tbl = 'receipts'")" == 1 ]] || die "날짜 $dn 생성 실패"
    log "generate: 날짜 $dn 끝 $(( $(date +%s) - s ))초"
    chunk=-1
  done
}

after_generate() {
  local t start
  mkdir -p "$OUT"
  start=$(date +%s)
  {
    echo "# after-generate $(utc)"
    Q -c '\timing on' -c 'ANALYZE'
    Q -c "SELECT count(*) AS not_valid_constraints FROM pg_constraint WHERE NOT convalidated"
    Q -c "SELECT relname, n_live_tup, pg_total_relation_size(relid) AS bytes FROM pg_stat_user_tables
          WHERE relname NOT LIKE 'x7b%' ORDER BY bytes DESC"
    Q -c "SELECT pg_database_size(current_database()) AS db_bytes"
  } > "$OUT/after-generate.txt" 2>&1
  [[ "$(V "SELECT count(*) FROM pg_constraint WHERE NOT convalidated")" == 0 ]] || die "검증되지 않은 제약이 있다"
  # 생성 중 autovacuum이 다 세우지 못한 visibility map을 운영처럼 세운다(회차 안 VACUUM이 바뀐 페이지만 읽게)
  for t in $TABLES4; do Q -c '\timing on' -c "VACUUM (VERBOSE) $t"; done > "$OUT/after-generate-vacuum.txt" 2>&1
  Q -q -c "SELECT pg_stat_reset()" > /dev/null
  if [[ "$(V "SELECT count(*) FROM pg_extension WHERE extname = 'pg_stat_statements'")" == 1 ]]; then
    Q -q -c "SELECT pg_stat_statements_reset()" > /dev/null
  fi
  log "after-generate: ANALYZE·VACUUM $(( $(date +%s) - start ))초, 통계 초기화"
}

account() { V "SELECT account_id FROM x7b_gu WHERE u = ${1:?사용자 번호}"; }

statio() {
  Q -At -F'|' -c "SELECT 'T', relname, coalesce(heap_blks_read,0), coalesce(idx_blks_read,0),
      coalesce(toast_blks_read,0) + coalesce(tidx_blks_read,0) FROM pg_statio_user_tables" \
    -c "SELECT 'I', indexrelname, coalesce(idx_blks_read,0) FROM pg_statio_user_indexes" \
    -c "SELECT 'W', 'wal_lsn', pg_current_wal_lsn() - '0/0'::pg_lsn"
}

sampler() {  # 1초마다: 가장 오래된 트랜잭션(나이 초, age(backend_xmin), pid, 질의 앞부분), 1초 넘게 기다린 잠금
  local o=$1
  while [[ ! -f "$o/STOP" ]]; do
    psql -X -At -F, -c "SELECT clock_timestamp(), coalesce(max(extract(epoch FROM clock_timestamp() - xact_start)), 0)::int,
        coalesce(max(age(backend_xmin)), 0), (array_agg(pid ORDER BY xact_start))[1],
        replace(left((array_agg(query ORDER BY xact_start))[1], 60), ',', ' ')
      FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND xact_start IS NOT NULL AND backend_type = 'client backend'" \
      >> "$o/xact.csv" 2>> "$o/sampler.err" || true
    psql -X -At -F, -c "SELECT clock_timestamp(), l.pid, round(extract(epoch FROM clock_timestamp() - l.waitstart)::numeric, 1),
        l.mode, coalesce(l.relation::regclass::text, l.locktype), replace(left(a.query, 60), ',', ' ')
      FROM pg_locks l JOIN pg_stat_activity a USING (pid)
      WHERE NOT l.granted AND l.waitstart < clock_timestamp() - interval '1 second'" \
      >> "$o/locks.csv" 2>> "$o/sampler.err" || true
    sleep 1
  done
}

vacuum4() { local t; for t in $TABLES4; do Q -c '\timing on' -c "VACUUM (VERBOSE) $t"; done 2>&1; }

summarize() {  # 전후 statio 차이 → summary.txt
  local o=$1 rows=$2
  awk -F'|' -v rows="$rows" '
    FNR == NR { if ($1 == "T") { h[$2] = $3; i[$2] = $4 } else if ($1 == "W") w = $3; next }
    $1 == "T" { dh += $3 - h[$2]; di += $4 - i[$2] }
    $1 == "W" { dw = $3 - w }
    END { printf "heap_read %d\nidx_read %d\ntotal_read %d\nrecord_rows %d\nread_per_record_row %.2f\nwal_bytes %d\n",
                 dh, di, dh + di, rows, (rows > 0 ? (dh + di) / rows : 0), dw }' "$o/before.txt" "$o/after.txt"
  grep -h -E 'NOTICE:  (records|total)' "$o/sql.txt" | sed 's/^NOTICE:  /notice /'
  echo "max_xact_sec $(cut -d, -f2 "$o/xact.csv" 2>/dev/null | sort -n | tail -1)"
  echo "max_xmin_age $(cut -d, -f3 "$o/xact.csv" 2>/dev/null | sort -n | tail -1)"
  echo "lock_waits_over_1s $(wc -l < "$o/locks.csv" | tr -d ' ')"
  if [[ -f "$o/vacuum_during.txt" ]]; then
    echo "not_removable_during $(grep -o '[0-9]* are dead but not yet removable' "$o/vacuum_during.txt" | awk '{s += $1} END {print s + 0}')"
    echo "not_removable_after $(grep -o '[0-9]* are dead but not yet removable' "$o/vacuum_after.txt" | awk '{s += $1} END {print s + 0}')"
  fi
  cat "$o/load.txt" 2>/dev/null || true
}

delete_run() {
  local name=${1:?이름} mode=${2:?one|day} id=${3:?계정} day=${4:-} load=${5:-} o rows sp ap=""
  [[ "$day" == - ]] && day=""
  [[ "$mode" == one || "$mode" == day ]] || die "삭제 방식은 one|day"
  o="$OUT/$name"
  [[ -e "$o" ]] && die "$o가 이미 있다(같은 삭제를 두 번 돌리지 않는다)"
  mkdir -p "$o"
  [[ -n "$load" ]] && echo "load=$load" > "$o/load.txt"
  rows=$(V "SELECT count(*) FROM activity_records r JOIN devices d ON d.id = r.device_id WHERE d.account_id = '$id'")
  echo "account=$id mode=$mode archive_day=${day:-none} record_rows=$rows start=$(utc)" > "$o/account.txt"
  (( rows > 0 )) || die "계정 $id에 레코드가 없다"
  statio > "$o/before.txt"
  sampler "$o" & sp=$!
  if [[ -n "$day" ]]; then
    { Q -c '\timing on' -c "CALL x7b_archive_delete('$day', 'rows', 2000)" > "$o/archive.txt" 2>&1
      echo "end=$(utc)" >> "$o/archive.txt"
      vacuum4 > "$o/vacuum_during.txt"; } & ap=$!
  fi
  log "delete $name: $mode 계정 $id(레코드 ${rows}행), 이관 ${day:-없음}"
  set +e
  Q -c '\timing on' -c "CALL x7b_account_delete('$id', '$mode')" > "$o/sql.txt" 2>&1
  echo "rc=$?" >> "$o/sql.txt"
  set -e
  echo "end=$(utc)" >> "$o/sql.txt"
  [[ -n "$ap" ]] && wait "$ap"
  sleep 2   # 끝난 세션의 통계가 공유 통계에 반영되기를 기다린다
  statio > "$o/after.txt"
  [[ -n "$day" ]] && vacuum4 > "$o/vacuum_after.txt"
  touch "$o/STOP"; wait "$sp"
  summarize "$o" "$rows" > "$o/summary.txt"
  log "delete $name 끝: $(tr '\n' ' ' < "$o/summary.txt")"
  grep -q '^rc=0$' "$o/sql.txt" || die "delete $name 실패($o/sql.txt)"
}

minus_days() { V "SELECT ('${1:?}'::date - ${2:?})::text"; }
start_at() { cat "$W/k6out/$1.start" 2>/dev/null || true; }

hooks() {  # r2 = L1('day' + 이관 D−90), r3 = L2('one' + 이관 D−89). 회차 시작 + 웜업 + HOOK_GAP 뒤 시작
  local d=${1:?D} a1=${2:?L1 계정} a2=${3:?L2 계정} t now load
  log "hooks: D=$d L1=$a1 L2=$a2, 웜업 ${MIXED_WARM}s + ${HOOK_GAP}s"
  until [[ -n "$(start_at mixed-r2)" || -f "$W/k6out/hooks.stop" ]]; do sleep 2; done
  t=$(start_at mixed-r2)
  if [[ -n "$t" ]]; then
    now=$(date +%s); (( now < t + MIXED_WARM + HOOK_GAP )) && sleep $(( t + MIXED_WARM + HOOK_GAP - now ))
    load=r2; [[ -f "$W/k6out/mixed-r2.rc" ]] && load=none
  else load=none; fi
  delete_run L1 day "$a1" "$(minus_days "$d" 90)" "$load"
  # L2: "L1 끝"과 "r3 시작 + 웜업 + HOOK_GAP" 중 늦은 때. r3가 이미 끝났거나 시작하지 않으면 부하 없이
  until [[ -n "$(start_at mixed-r3)" || -f "$W/k6out/hooks.stop" ]]; do sleep 2; done
  t=$(start_at mixed-r3)
  if [[ -n "$t" ]]; then
    now=$(date +%s); (( now < t + MIXED_WARM + HOOK_GAP )) && sleep $(( t + MIXED_WARM + HOOK_GAP - now ))
    load=r3; [[ -f "$W/k6out/mixed-r3.rc" ]] && load=none
  else load=none; fi
  delete_run L2 one "$a2" "$(minus_days "$d" 89)" "$load"
  log "hooks 끝"
}

verify() {  # 생성 결과 검사. 하나라도 어긋나면 종료 코드 1
  local bad
  bad=$(V "
    WITH rec AS (SELECT d.account_id, (r.observed_at AT TIME ZONE 'Asia/Seoul')::date AS ld, count(*) n
            FROM activity_records r JOIN devices d ON d.id = r.device_id GROUP BY 1, 2),
    rcp AS (SELECT account_id, (processed_at AT TIME ZONE 'Asia/Seoul')::date AS ld, count(*) n FROM activity_batch_receipts GROUP BY 1, 2),
    ses AS (SELECT account_id, (started_at AT TIME ZONE 'Asia/Seoul')::date AS ld, count(*) n FROM focus_sessions GROUP BY 1, 2),
    seg AS (SELECT account_id, (started_at AT TIME ZONE 'Asia/Seoul')::date AS ld,
                   count(*) FILTER (WHERE segment_type = 'activity') act, count(*) FILTER (WHERE segment_type = 'capture_gap') gap
            FROM activity_timeline_segments GROUP BY 1, 2),
    lbl AS (SELECT a.id, count(l.id) n FROM accounts a LEFT JOIN labels l ON l.account_id = a.id GROUP BY 1)
    SELECT 'records_per_day', count(*) FROM rec WHERE n <> 5500
    UNION ALL SELECT 'receipts_per_day', count(*) FROM rcp WHERE n <> 2934
    UNION ALL SELECT 'sessions_per_day', count(*) FROM ses WHERE n <> 3
    UNION ALL SELECT 'segments_per_day', count(*) FROM seg WHERE act <> 410 OR gap <> 1
    UNION ALL SELECT 'labels_per_account', count(*) FROM lbl WHERE n <> 6
    UNION ALL SELECT 'days_per_account', count(*) FROM (SELECT account_id FROM rec GROUP BY 1
              HAVING count(*) <> (SELECT v::int FROM x7b_gconf WHERE k = 'days')) x
    UNION ALL SELECT 'seg_first_missing', count(*) FROM activity_timeline_segments s
              WHERE NOT EXISTS (SELECT 1 FROM activity_records r WHERE r.event_id = s.first_event_id)
    UNION ALL SELECT 'seg_last_missing', count(*) FROM activity_timeline_segments s
              WHERE s.last_event_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM activity_records r WHERE r.event_id = s.last_event_id)
    UNION ALL SELECT 'conf_first_missing', count(*) FROM activity_label_confirmations c
              WHERE NOT EXISTS (SELECT 1 FROM activity_records r WHERE r.event_id = c.first_event_id)
    UNION ALL SELECT 'prop_first_missing', count(*) FROM activity_label_proposals p
              WHERE NOT EXISTS (SELECT 1 FROM activity_records r WHERE r.event_id = p.first_event_id)
    UNION ALL SELECT 'rec_session_missing', count(*) FROM activity_records r
              WHERE r.focus_session_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM focus_sessions f WHERE f.id = r.focus_session_id)
    UNION ALL SELECT 'seg_other_account', count(*) FROM activity_timeline_segments s
              JOIN activity_records r ON r.event_id = s.first_event_id JOIN devices d ON d.id = r.device_id WHERE d.account_id <> s.account_id
    UNION ALL SELECT 'conf_label_other_account', count(*) FROM activity_label_confirmations c
              JOIN labels l ON l.id = c.label_id WHERE l.account_id <> c.account_id
    UNION ALL SELECT 'prop_label_other_account', count(*) FROM activity_label_proposals p
              JOIN labels l ON l.id = p.suggested_label_id WHERE l.account_id <> p.account_id
    UNION ALL SELECT 'not_valid_constraints', count(*) FROM pg_constraint WHERE NOT convalidated
    UNION ALL SELECT 'scatter_below_0.95', count(*) FROM (
              SELECT r.device_id, count(DISTINCT (r.ctid::text::point)[0])::numeric / count(*) AS ratio
              FROM activity_records r GROUP BY 1) x WHERE ratio < 0.95" | tr '|' ' ')
  echo "$bad"
  if awk '$2 != 0 { exit 1 }' <<< "$bad"; then log "verify: 모두 0"; else log "verify: 어긋남"; return 1; fi
}

contexts() { V "SELECT ctx::text FROM x7b_ctx ORDER BY i"; }

cleanup() {
  [[ "${1:-}" == --yes ]] || die "cleanup은 --yes가 필요하다(생성 데이터를 모두 지운다)"
  [[ "$(V "SELECT to_regclass('x7b_gconf') IS NOT NULL")" == t ]] || die "x7b_gconf가 없다. 7B 생성 DB가 아니다"
  Q <<'SQL'
\set ON_ERROR_STOP 1
TRUNCATE accounts CASCADE;
DROP INDEX IF EXISTS x7b_seg_first, x7b_seg_last, x7b_conf_first, x7b_prop_first, x7b_conf_label, x7b_prop_label;
ALTER TABLE activity_records RESET (autovacuum_vacuum_scale_factor);
ALTER TABLE activity_timeline_segments RESET (autovacuum_vacuum_scale_factor);
ALTER TABLE activity_label_confirmations RESET (autovacuum_vacuum_scale_factor);
ALTER TABLE activity_label_proposals RESET (autovacuum_vacuum_scale_factor);
DROP PROCEDURE IF EXISTS x7b_gen_setup(int, date), x7b_gen_day(int, int), x7b_account_delete(uuid, text), x7b_archive_delete(date, text, int);
DROP FUNCTION IF EXISTS x7b_v7(timestamptz, text), x7b_wall(timestamptz, float8), x7b_a(float8, int),
  x7b_ev(timestamptz, float8, int, int, int), x7b_s0(int), x7b_sk(int), x7b_win(float8);
DROP TABLE IF EXISTS x7b_gconf, x7b_gu, x7b_ctx, x7b_glog;
SQL
  log "cleanup: 실험 객체와 생성 데이터를 지웠다"
}

case "${1:-}" in
  prepare) prepare ;;
  generate) shift; generate "$@" ;;
  after-generate) after_generate ;;
  account) account "${2:-}" ;;
  delete) shift; delete_run "$@" ;;
  hooks) shift; hooks "$@" ;;
  verify) verify ;;
  contexts) contexts ;;
  cleanup) cleanup "${2:-}" ;;
  *) sed -n '2,19p' "$0"; exit 2 ;;
esac

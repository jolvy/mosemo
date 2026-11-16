#!/usr/bin/env bash
# 7B 계정 삭제 운영 규모 측정 Mac 쪽 실행 스크립트. 측정 A 도구(measurement-pod.sh push, run-load-measurement.sh
# check·push·mixed·final·fetch, db-measurement-steps.sh a0)를 그대로 쓰고, DB 쪽 7B 절차는 delete-scale-steps.sh로 pg 컨테이너에서 돌린다.
#
#   순서(all): check → push(하네스·head 파일, k6 도구, 7B 파일) → a0(빈 DB 관문) → 시간 검사 → prepare → generate(pg, detach)
#              → after-generate(ANALYZE·제약·행 수·4개 표 VACUUM 1회·통계 초기화) + inventory(--today D)
#              → L1a(부하 없이 'day' 삭제) → hooks(pg, detach) → mixed(3회차, 삭제 대상 k6 제외) → hooks 종료 대기 → final → fetch
#     r1 = L0(부하만), r2 = L1('day' + 이관 D−90), r3 = L2('one' + 이관 D−89). 삭제 대상은 사용자 3·4·5(x7b_gu.u).
#   사용: run-delete-scale-measurement.sh <all|check|push|a0|generate|after|l1a|measure|fetch>
#   환경: MEASURE_DATE(D, YYYY-MM-DD, 필수), USERS(300), DAYS(91, 첫날은 항상 D−90), DEADLINE_UTC(랩 필수),
#         RESUME=1(생성 재개: a0·prepare를 건너뜀), STATE(~/mosemo-step5/kube-delete-scale), MIXED_WARM(60), HOOK_GAP(30),
#         SEC_PER_USER_DAY(0.66, 생성 시간 검사용), DRY_RUN=1(클러스터에 닿지 않고 명령만 출력)
#   LOCAL=1(local-dryrun.sh가 부른다): W·PY·ENV_FILE·RDS_PG를 받아 pg 쪽 명령을 PostgreSQL 18.3 일회용 컨테이너(Pod pg 컨테이너와 같은 digest)로 돌린다.
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL="${LOCAL:-0}"
DRY_RUN="${DRY_RUN:-0}"
MEASUREMENT_KUBECONFIG="${MEASUREMENT_KUBECONFIG:-$HOME/.mosemo/lab/measurement.kubeconfig}"
NS=mosemo
POD=step5-tools
STATE="${STATE:-$HOME/mosemo-step5/kube-delete-scale}"
D="${MEASURE_DATE:?MEASURE_DATE(측정 날 D, KST 날짜)를 준다}"
USERS="${USERS:-300}"
DAYS="${DAYS:-91}"
MIXED_WARM="${MIXED_WARM:-60}"
HOOK_GAP="${HOOK_GAP:-30}"
SEC_PER_USER_DAY="${SEC_PER_USER_DAY:-0.66}"
RESUME="${RESUME:-0}"
FILES=(delete-scale-steps.sh delete-scale-prepare.sql delete-scale-generate.sql account-delete-procedure.sql archive-delete-procedure.sql)
FIRST=$(python3 -c "import datetime,sys;print(datetime.date.fromisoformat(sys.argv[1])-datetime.timedelta(days=90))" "$D")
export MIXED_WARM HOOK_GAP

if [[ "$LOCAL" == 1 ]]; then
  W="${W:?LOCAL=1은 W를 준다}"; PY="${PY:?}"; ENV_FILE="${ENV_FILE:?}"; RDS_PG="${RDS_PG:-m7a-rds}"
  S5DIR="$ART"
  DEADLINE_UTC="${DEADLINE_UTC:-$(date -u -v+3H +%Y-%m-%dT%H:%M:%SZ)}"
else
  W=/work; PY=python; S5DIR=/work/step5
  DEADLINE_UTC="${DEADLINE_UTC:?DEADLINE_UTC(잠금 토큰 만료 전 UTC)를 준다}"
fi
export DEADLINE_UTC STATE
OUTD="$W/k6out"
STEPS="$S5DIR/delete-scale-steps.sh"
LOAD="$ART/run-load-measurement.sh"

mkdir -p "$STATE"
utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(utc)] delete-scale: $*" | tee -a "$STATE/delete-scale.log" >&2; }
die() { log "실패: $*"; exit 1; }
K() {
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ KUBECONFIG=$MEASUREMENT_KUBECONFIG kubectl -n $NS $*" >&2; return 0; fi
  KUBECONFIG="$MEASUREMENT_KUBECONFIG" kubectl -n "$NS" "$@"
}
local_pg() {  # local_pg [docker 옵션...] -- <명령...>: Pod의 pg 컨테이너 대역(PostgreSQL 18.3, /work 공유, 도구는 /x7b 읽기 전용)
  local opts=() db_user db_password db_database
  while [[ "$1" != -- ]]; do opts+=("$1"); shift; done; shift
  db_user=$(sed -n 's/^DB_USER=//p' "$ENV_FILE"); db_password=$(sed -n 's/^DB_PASSWORD=//p' "$ENV_FILE")
  db_database=$(sed -n 's/^DB_DATABASE=//p' "$ENV_FILE")
  PGPASSWORD="$db_password" docker run --rm ${opts[@]+"${opts[@]}"} --network "container:$RDS_PG" -v "$W:/work" -v "$ART:/x7b:ro" \
    -e PGHOST=127.0.0.1 -e PGPORT=5432 -e PGUSER="$db_user" -e PGPASSWORD -e PGDATABASE="$db_database" \
    -e W=/work -e MIXED_WARM -e HOOK_GAP postgres:18.3@sha256:7e32e9833a6fb1c92c32552794cb6ed569d51b445a54907d35fc112ef39684db "$@"
}
PGX() {  # pg 컨테이너 안 명령(문자열 하나)
  local cmd=$1
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ (pg) $cmd" >&2; return 0; fi
  if [[ "$LOCAL" == 1 ]]; then local_pg -i -- bash -c "${cmd//$STEPS//x7b/delete-scale-steps.sh}"
  else K exec "$POD" -c pg -- env W=/work MIXED_WARM="$MIXED_WARM" HOOK_GAP="$HOOK_GAP" bash -c "$cmd"; fi
}
PGQ() {  # 출력이 필요한 pg 명령. DRY_RUN이면 자리 값
  local dry=$1 cmd=$2
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ (pg) $cmd" >&2; echo "$dry"; return 0; fi
  PGX "$cmd"
}
TOOLSX() {  # tools 컨테이너 안 명령. LOCAL이면 Mac에서 ENV_FILE을 넣고
  local cmd=$1
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ (tools) $cmd" >&2; return 0; fi
  if [[ "$LOCAL" == 1 ]]; then (set -a; source "$ENV_FILE"; set +a; bash -c "$cmd")
  else K exec "$POD" -c tools -- bash -c "$cmd"; fi
}
detach_pg() {  # detach_pg <이름> <명령>: 끊겨도 계속 돈다. 끝나면 $OUTD/<이름>.rc
  local name=$1 cmd=$2
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ (pg, detach $name) $cmd" >&2; return 0; fi
  mkdir -p "$STATE"
  if [[ "$LOCAL" == 1 ]]; then
    mkdir -p "$OUTD"; rm -f "$OUTD/$name.rc"
    local_pg -d --name "x7b-$name-$$" -- \
      bash -c "{ ${cmd//$STEPS//x7b/delete-scale-steps.sh}; } > /work/k6out/$name.out 2>&1; echo \$? > /work/k6out/$name.rc" > /dev/null
  else
    K exec "$POD" -c pg -- bash -c "mkdir -p $OUTD; rm -f $OUTD/$name.rc; W=/work MIXED_WARM=$MIXED_WARM HOOK_GAP=$HOOK_GAP \
      setsid nohup bash -c '{ $cmd; } > $OUTD/$name.out 2>&1; echo \$? > $OUTD/$name.rc' > /dev/null 2>&1 < /dev/null &"
  fi
}
DEADLINE=$(python3 -c "import datetime,sys;print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$DEADLINE_UTC")
rc_of() {
  if [[ "$LOCAL" == 1 ]]; then cat "$OUTD/$1.rc" 2>/dev/null || true
  else K exec "$POD" -c pg -- cat "$OUTD/$1.rc" 2>/dev/null || true; fi
}
wait_rc() {  # wait_rc <이름>: 종료 코드, 시간 상한이면 deadline
  local name=$1 rc
  [[ "$DRY_RUN" == 1 ]] && { echo 0; return 0; }
  while :; do
    rc=$(rc_of "$name")
    [[ -n "$rc" ]] && { echo "$rc"; return 0; }
    (( $(date +%s) + 600 > DEADLINE )) && { echo deadline; return 0; }
    sleep 10
  done
}
touch_pg() {
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ (pg) touch $1" >&2
  elif [[ "$LOCAL" == 1 ]]; then mkdir -p "$(dirname "$W${1#"$W"}")"; touch "$W${1#"$W"}"
  else K exec "$POD" -c pg -- touch "$1"; fi
}

step_check() { "$LOAD" check; }

step_push() {
  local f head head_file
  if [[ "$LOCAL" == 1 ]]; then log "push(LOCAL): 저장소 폴더의 도구를 그대로 씀"; return 0; fi
  head=$(cd "$ART/../.." && uv run --locked alembic heads 2>/dev/null | awk 'NF {print $1; exit}')
  [[ "$DRY_RUN" == 1 || -n "$head" ]] || die "alembic heads를 읽지 못했다"
  echo "${head:-<head>}" > "$STATE/alembic-head.txt"
  head_file="$STATE/alembic-head.txt"   # 한 줄의 앞 할당(STATE)이 뒤 할당에 보이므로 먼저 계산한다
  STATE="$STATE/pod" ALEMBIC_FILE="$head_file" "$ART/measurement-pod.sh" push --no-dump
  "$LOAD" push
  for f in "${FILES[@]}"; do K cp "$ART/$f" "$NS/$POD:/work/step5/$f" -c tools; done
  { for f in "${FILES[@]}"; do echo "$(shasum -a 256 "$ART/$f" | awk '{print $1}')  $f"; done
    echo "commit $(git -C "$ART" rev-parse HEAD 2>/dev/null)"; } > "$STATE/push-delete-scale-sha256.txt"
  log "push: 7B 파일 ${#FILES[@]}개(sha256 push-delete-scale-sha256.txt), alembic head ${head:-<head>}"
}

step_a0() {
  [[ "$RESUME" == 1 ]] && { log "a0: RESUME=1이라 건너뜀"; return 0; }
  local cmd="cd $S5DIR && bash $S5DIR/db-measurement-steps.sh a0"
  if [[ "$LOCAL" == 1 ]]; then
    (cd "$ART/../.." && uv run --locked alembic heads 2>/dev/null | awk 'NF {print $1; exit}') > "$STATE/alembic-head.txt"
    cmd="TARGET=local W=$W PY=$PY ENV_FILE=$ENV_FILE DUMP=none ALEMBIC_FILE=$STATE/alembic-head.txt RDS_PG=$RDS_PG bash $ART/db-measurement-steps.sh a0"
  fi
  TOOLSX "$cmd" || die "a0 관문 실패"
}

step_generate() {
  local need rc
  need=$(python3 -c "print(int($USERS * $DAYS * $SEC_PER_USER_DAY) + 5400)")
  (( $(date +%s) + need <= DEADLINE )) || die "생성 ${USERS}명 × ${DAYS}일 + 측정에 약 ${need}초가 필요한데 시간 상한($DEADLINE_UTC)을 넘는다"
  [[ "$RESUME" == 1 ]] || PGX "bash $STEPS prepare"
  log "generate: ${USERS}명 × ${DAYS}일, 첫날 $FIRST(D−90), D $D"
  detach_pg generate "bash $STEPS generate $FIRST $USERS $DAYS"
  rc=$(wait_rc generate)
  [[ "$rc" == 0 ]] || die "generate 종료 코드 $rc($OUTD/generate.out). RESUME=1로 이어서 돌린다"
}

step_after() {
  local rc
  detach_pg after "bash $STEPS after-generate"
  rc=$(wait_rc after)
  [[ "$rc" == 0 ]] || die "after-generate 종료 코드 $rc($OUTD/after.out)"
  TOOLSX "cd $S5DIR && $PY db_harness.py inventory --out $W/results/inventory.json --today $D" || die "inventory 실패"
  log "after: inventory --today $D"
}

ACC3="" ACC4="" ACC5="" MIXED_RC=0
accounts() {
  ACC3=$(PGQ "<acct-3>" "bash $STEPS account 3"); ACC4=$(PGQ "<acct-4>" "bash $STEPS account 4")
  ACC5=$(PGQ "<acct-5>" "bash $STEPS account 5")
  [[ -n "$ACC3" && -n "$ACC4" && -n "$ACC5" ]] || die "삭제 대상 계정을 읽지 못했다"
}

step_l1a() {
  local rc
  accounts
  detach_pg l1a "bash $STEPS delete L1a day $ACC3 - none"
  rc=$(wait_rc l1a)
  [[ "$rc" == 0 ]] || die "L1a 종료 코드 $rc($OUTD/l1a.out)"
}

step_measure() {
  local rc
  accounts
  touch_pg "$OUTD/.keep"
  detach_pg hooks "bash $STEPS hooks $D $ACC4 $ACC5"
  log "measure: r1 = L0, r2 = L1(계정 사용자 4), r3 = L2(계정 사용자 5). k6 제외: 사용자 3·4·5"
  set +e
  MIXED_RUNS=3 EXCLUDE_ACCOUNTS="$ACC3,$ACC4,$ACC5" "$LOAD" mixed
  rc=$?
  set -e
  log "mixed 종료 코드 $rc"
  MIXED_RC=$rc
  touch_pg "$OUTD/hooks.stop"   # 회차가 일찍 끝났으면 남은 삭제를 부하 없이 돌린다
  rc=$(wait_rc hooks)
  [[ "$rc" == 0 ]] || log "hooks 종료 코드 $rc($OUTD/hooks.out)"
}

step_fetch() { "$LOAD" final || log "final 확인 실패"; "$LOAD" fetch; }

case "${1:-}" in
  check) step_check ;;
  push) step_push ;;
  a0) step_a0 ;;
  generate) step_generate ;;
  after) step_after ;;
  l1a) step_l1a ;;
  measure) step_measure; [[ "$MIXED_RC" == 0 ]] || die "mixed 종료 코드 $MIXED_RC" ;;
  fetch) step_fetch ;;
  all)
    step_check; step_push; step_a0; step_generate; step_after; step_l1a; step_measure; step_fetch
    [[ "$MIXED_RC" == 0 ]] || die "mixed 종료 코드 $MIXED_RC(결과는 회수함)"
    ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac

#!/usr/bin/env bash
# 측정 A(7단계 뒤 기준 사양 재측정) Mac 쪽 실행 스크립트. STEP7A-PREREG.md가 고정한 순서를 그대로 돈다.
# 5단계 k6 한계 측정 스크립트의 mixed 경로 사본에 다음을 더했다:
#   check  batch Deployment 없음·API/consumer 수·이미지 기록(측정 전 확인)
#   prep   db-measurement-steps.sh를 Pod에 넣고 consumer 0 → a0(관문) → a1(복원) → a2(재배치·VACUUM·inventory)
#   mixed  consumer CONSUMERS개 → 회차마다 rec before(질의별 통계 초기화) → k6 → 유예 → rec after → 판정
#   final  측정 끝 상태(일간 행 0 = batch 미실행, 표시 분포), 원래 consumer 수로 되돌림
#   post-dump  측정 직후 DB 덤프(Pod 안, POST_DUMP=1일 때만 all에 포함), fetch-dump로 회수
#   fetch  결과 묶음(k6 CSV·판정·질의별 통계·DB 상태) 회수, sha256 비교
#   all    check → prep → mixed → final → [post-dump] → fetch
# 기본값이 측정 A 값이다: MIXED_TOTAL=137.6, CONSUMERS=4, PARTITIONS=8, 15분 × 2회, 웜업·유예 60초.
# DEADLINE_UTC는 기본값이 없다(잠금 토큰 만료 전 시각을 반드시 준다).
#
#   사용: run-load-measurement.sh <check|prep|mixed|final|post-dump|fetch|fetch-dump|all>
#   환경: MEASUREMENT_KUBECONFIG, DEADLINE_UTC, STATE(기본 ~/mosemo-step5/kube-7a), EXPECT_HEAD(c5d6e7f8a9b0),
#         MIXED_TOTAL·MIXED_SEC·MIXED_RUNS·MIXED_WARM·MIXED_GRACE·CONSUMERS·PARTITIONS, POST_DUMP(0), K6_LOCAL, DRY_RUN=1
#   LOCAL=1(드라이런): kubectl 대신 Mac에서 바로 실행. W·ENV_FILE·PY·K6_BIN·API_URL을 local-dryrun.sh가 준다.
#         consumer 수 조절·batch Deployment 확인은 건너뛰고(로컬 프로세스) 그 사실을 로그에 남긴다.
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL="${LOCAL:-0}"
MEASUREMENT_KUBECONFIG="${MEASUREMENT_KUBECONFIG:-$HOME/.mosemo/lab/measurement.kubeconfig}"
NS=mosemo
POD=step5-tools
CONSUMER=mosemo-consumer
STATE="${STATE:-$HOME/mosemo-step5/kube-7a}"
DRY_RUN="${DRY_RUN:-0}"
EXPECT_HEAD="${EXPECT_HEAD:-c5d6e7f8a9b0}"
MIXED_TOTAL="${MIXED_TOTAL:-137.6}"
MIXED_SEC="${MIXED_SEC:-900}"
MIXED_RUNS="${MIXED_RUNS:-2}"
MIXED_WARM="${MIXED_WARM:-60}"
MIXED_GRACE="${MIXED_GRACE:-60}"
CONSUMERS="${CONSUMERS:-4}"
PARTITIONS="${PARTITIONS:-8}"
POST_DUMP="${POST_DUMP:-0}"
WORKLOAD="workload-${MIXED_TOTAL}rps.json"
WORKLOAD_SHA=05eb76f76af02112ae556de589c3832ef6c8b4eb5bc0e05fc9fb892444d22d29   # 5단계와 같은 파일이어야 한다
K6_VERSION=v2.3.0
K6_LOCAL="${K6_LOCAL:-$HOME/mosemo-step5/k6/v2.3.0/k6-v2.3.0-linux-amd64/k6}"
K6_SHA256=e001bbf39ba67424a85c9c8516440ba5c73ecf7a6021be96c76194380c15f260
FETCH_MARGIN=600
FILES=(k6_inputs_and_reports.py k6-common.js k6-mixed-read-upload.js
       db-measurement-steps.sh)

if [[ "$LOCAL" == 1 ]]; then
  W="${W:?LOCAL=1은 W를 준다}"; PY="${PY:?}"; K6_BIN="${K6_BIN:?}"; API_URL="${API_URL:?}"; ENV_FILE="${ENV_FILE:?}"
  S5DIR="$ART"                          # 도구는 산출물 폴더에서 바로 쓴다
  REPO="${S5_REPO:-$(cd "$ART/../.." && pwd)}"
  DEADLINE_UTC="${DEADLINE_UTC:-$(date -u -v+3H +%Y-%m-%dT%H:%M:%SZ)}"
else
  W=/work; PY=python; K6_BIN=/work/k6; S5DIR=/work/step5; REPO=/work/repo
  API_URL="${API_URL:-http://mosemo-server.mosemo.svc.cluster.local}"
  DEADLINE_UTC="${DEADLINE_UTC:?DEADLINE_UTC(잠금 토큰 만료 전 UTC)를 준다}"
fi
OUTD="$W/k6out"
RUNSH="$S5DIR/db-measurement-steps.sh"

# STATE가 저장소 안이면 멈춘다(기록에 계정 ID가 든 ECR URI가 남아 커밋될 수 있다). 경로를 루트부터 한 단계씩 따라가며
# 이미 있는 폴더는 물리 경로로 풀고(..·심볼릭 링크 해소, mkdir -p와 같은 순서), 아직 없는 부분은 새로 만들 폴더로 센다.
# 그렇게 얻은 가장 깊은 기존 폴더나 그 상위 폴더가 저장소 루트와 같은 폴더(inode 비교: 대소문자·firmlink 무관)이면 멈춘다.
# log·die는 STATE에 쓰므로 아직 쓰지 않는다.
refuse_state_in_repo() {
  local p=$1 base=/ c depth=0 repo d parts=()
  [[ "$p" != *$'\n'* ]] || { echo "STATE에 줄바꿈이 있다" >&2; exit 2; }   # read는 첫 줄만 판정한다
  [[ "$p" == /* ]] || p="$PWD/$p"
  IFS=/ read -ra parts <<< "$p"
  for c in ${parts[@]+"${parts[@]}"}; do
    case "$c" in
      ''|.) ;;
      ..) if (( depth > 0 )); then depth=$((depth - 1)); else base=${base%/*}; [[ -n "$base" ]] || base=/; fi ;;
      *) if (( depth == 0 )) && [[ -d "$base/$c" ]] && d=$(cd "$base/$c" 2>/dev/null && /bin/pwd -P); then base=$d
         else depth=$((depth + 1)); fi ;;
    esac
  done
  repo=$(cd "$ART/../.." && /bin/pwd -P)
  d=$base
  while :; do
    if [[ "$d" -ef "$repo" ]]; then echo "STATE가 저장소 안이다($1). 저장소 밖 폴더를 준다" >&2; exit 2; fi
    [[ "$d" != / ]] || break
    d=${d%/*}; [[ -n "$d" ]] || d=/
  done
}
refuse_state_in_repo "$STATE"
mkdir -p "$STATE"
utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "[$(utc)] step7a: $*" | tee -a "$STATE/step7a.log" >&2; }
die() { log "실패: $*"; exit 1; }
K() {
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ KUBECONFIG=$MEASUREMENT_KUBECONFIG kubectl -n $NS $*" >&2; return 0; fi
  KUBECONFIG="$MEASUREMENT_KUBECONFIG" kubectl -n "$NS" "$@"
}
KQ() {
  local dry=$1; shift
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ KUBECONFIG=$MEASUREMENT_KUBECONFIG kubectl -n $NS $*" >&2; echo "$dry"; return 0; fi
  KUBECONFIG="$MEASUREMENT_KUBECONFIG" kubectl -n "$NS" "$@"
}
# 컨테이너 안 명령(문자열 하나). LOCAL이면 Mac에서 같은 문자열을 실행한다(ENV_FILE의 DB_*·AUTH_*를 넣고)
XC() {  # XC <tools|pg> <명령 문자열>
  local c=$1 cmd=$2
  if [[ "$LOCAL" == 1 ]]; then
    if [[ "$DRY_RUN" == 1 ]]; then echo "+ (로컬) $cmd" >&2; return 0; fi
    (set -a; source "$ENV_FILE"; set +a; export TARGET=local W PY S5_REPO="$REPO"; bash -c "$cmd")
  else
    K exec "$POD" -c "$c" -- bash -c "$cmd"
  fi
}
XQ() {  # 출력이 필요한 tools 명령. DRY_RUN이면 자리 값
  local dry=$1 cmd=$2
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ $cmd" >&2; echo "$dry"; return 0; fi
  XC tools "$cmd"
}
sha() { shasum -a 256 "$1" | awk '{print $1}'; }

DEADLINE=$(python3 -c "import datetime,sys;print(int(datetime.datetime.fromisoformat(sys.argv[1].replace('Z','+00:00')).timestamp()))" "$DEADLINE_UTC")
fits() {
  if (( $(date +%s) + $1 + FETCH_MARGIN <= DEADLINE )); then return 0; fi
  log "시간 상한($DEADLINE_UTC): $2 건너뜀"; echo "$2" >> "$STATE/skipped.txt"; return 1
}
detach() {  # detach <컨테이너> <이름> <명령 문자열>: exec가 끊겨도 계속 돈다. 끝나면 $OUTD/<이름>.rc
  local c=$1 name=$2 cmd=$3 bg="setsid nohup"
  [[ "$LOCAL" == 1 ]] && bg="nohup"   # macOS에는 setsid가 없다
  XC "$c" "mkdir -p $OUTD; rm -f $OUTD/$name.rc; $bg bash -c '{ $cmd; } > $OUTD/$name.out 2>&1; echo \$? > $OUTD/$name.rc' > /dev/null 2>&1 < /dev/null &"
}
wait_rc() {
  local name=$1 rc
  while :; do
    rc=$(XQ 0 "cat $OUTD/$name.rc 2>/dev/null" || true)
    [[ -n "$rc" ]] && { echo "$rc"; return 0; }
    (( $(date +%s) + FETCH_MARGIN > DEADLINE )) && { echo deadline; return 0; }
    [[ "$DRY_RUN" == 1 ]] || sleep 10
  done
}
P() { echo "cd $S5DIR && S5_REPO=$REPO $PY k6_inputs_and_reports.py $*"; }
R() { echo "cd $S5DIR && S5_REPO=$REPO bash $RUNSH $*"; }

# ---------------------------------------------------------------- check
step_check() {
  [[ "$(sha "$ART/$WORKLOAD")" == "$WORKLOAD_SHA" ]] || die "$WORKLOAD sha256이 5단계와 다르다"
  if [[ "$LOCAL" == 1 ]]; then
    pgrep -f "mosemo.entrypoints.batch" >/dev/null && die "로컬 batch 프로세스가 떠 있다"
    log "check(LOCAL): batch 프로세스 없음. consumer 수·Deployment 확인은 로컬이라 건너뜀"
    return 0
  fi
  [[ "$DRY_RUN" == 1 || -f "$MEASUREMENT_KUBECONFIG" ]] || die "kubeconfig 없음: $MEASUREMENT_KUBECONFIG"
  local deps srv_img con_img srv_av
  deps=$(KQ "mosemo-server mosemo-consumer" get deploy -o 'jsonpath={.items[*].metadata.name}')
  case " $deps " in *" mosemo-batch "*) die "mosemo-batch Deployment가 있다(D-52: 측정 A는 batch 0)";; esac
  srv_av=$(KQ 4 get deploy mosemo-server -o 'jsonpath={.status.availableReplicas}')
  [[ "$srv_av" == 4 ]] || die "mosemo-server Available $srv_av(4여야 함)"
  srv_img=$(KQ "<img>" get deploy mosemo-server -o 'jsonpath={.spec.template.spec.containers[0].image}')
  con_img=$(KQ "<img>" get deploy "$CONSUMER" -o 'jsonpath={.spec.template.spec.containers[0].image}')
  [[ "$srv_img" == "$con_img" ]] || die "API·consumer 이미지가 다르다($srv_img / $con_img)"
  echo "deployments=$deps server_available=$srv_av image=$srv_img checked=$(utc)" > "$STATE/check.txt"
  log "check: Deployment [$deps], batch 없음, API Available 4, 이미지 $srv_img"
}

# ---------------------------------------------------------------- consumer
consumer_record() {  # 원래 replicas를 한 번만 기록
  [[ "$LOCAL" == 1 ]] && return 0
  if [[ ! -s "$STATE/consumer-original.txt" ]]; then
    local n; n=$(KQ 4 get deploy "$CONSUMER" -o 'jsonpath={.spec.replicas}')
    [[ "$n" =~ ^[0-9]+$ ]] || die "consumer replicas 읽기 실패: $n"
    echo "$n" > "$STATE/consumer-original.txt"
  fi
}
consumer_set() {
  local want=$1 n
  if [[ "$LOCAL" == 1 ]]; then log "consumer $want(LOCAL: 로컬 프로세스 그대로)"; return 0; fi
  [[ "$want" =~ ^[0-9]+$ && "$want" -le "$PARTITIONS" ]] || die "CONSUMERS는 0 ~ ${PARTITIONS}"
  consumer_record
  K scale "deploy/$CONSUMER" --replicas="$want"
  for _ in $(seq 60); do
    n=$(KQ "$want" get deploy "$CONSUMER" -o 'jsonpath={.status.availableReplicas}')
    [[ "${n:-0}" == "$want" ]] && break
    [[ "$DRY_RUN" == 1 ]] || sleep 5
  done
  [[ "${n:-0}" == "$want" ]] || die "consumer Available ${n:-0}/${want}"
  log "consumer replicas ${want}(원래 $(cat "$STATE/consumer-original.txt"))"
}
consumer_restore() {
  [[ "$LOCAL" == 1 || ! -s "$STATE/consumer-original.txt" ]] && return 0
  local n; n=$(cat "$STATE/consumer-original.txt")
  K scale "deploy/$CONSUMER" --replicas="$n" && mv "$STATE/consumer-original.txt" "$STATE/consumer-original.restored.$(date -u +%Y%m%dT%H%M%SZ).txt"
  log "consumer replicas 원래 값 ${n}으로 되돌림"
}

# ---------------------------------------------------------------- push(도구) / prep(데이터 준비)
step_push() {
  if [[ "$LOCAL" == 1 ]]; then log "push(LOCAL): 산출물 폴더 도구를 그대로 씀"; return 0; fi
  local f pod_sha
  [[ "$DRY_RUN" == 1 || "$(sha "$K6_LOCAL")" == "$K6_SHA256" ]] || die "로컬 k6 sha256이 고정값과 다르다"
  K cp "$K6_LOCAL" "$NS/$POD:/work/k6" -c tools
  XC tools "chmod +x /work/k6"
  pod_sha=$(XQ "$K6_SHA256" "sha256sum /work/k6 | awk '{print \$1}'")
  [[ "$pod_sha" == "$K6_SHA256" ]] || die "Pod 안 k6 sha256 불일치($pod_sha)"
  for f in "${FILES[@]}" "$WORKLOAD"; do K cp "$ART/$f" "$NS/$POD:/work/step5/$f" -c tools; done
  { for f in "${FILES[@]}" "$WORKLOAD"; do echo "$(sha "$ART/$f")  $f"; done; echo "$K6_SHA256  k6"; } > "$STATE/push-sha256.txt"
  log "push: k6 ${K6_VERSION}·${FILES[*]}·$WORKLOAD(sha256 push-sha256.txt). 하네스·재배치 SQL·덤프는 measurement-pod.sh push가 먼저 넣었다"
}
step_prep() {
  local rc
  consumer_set 0
  log "prep a0(관문, 복원 전)"
  XC tools "$(R a0)" || die "a0 실패"
  log "prep a1(복원, pg 컨테이너)"
  detach pg a1 "$(R a1)"; rc=$(wait_rc a1); [[ "$rc" == 0 ]] || die "a1 종료 코드 $rc($OUTD/a1.out)"
  log "prep a2(관문·재배치·VACUUM·inventory)"
  detach tools a2 "$(R a2)"; rc=$(wait_rc a2); [[ "$rc" == 0 ]] || die "a2 종료 코드 $rc($OUTD/a2.out)"
  log "prep 끝: 복원 직후 상태 + 재배치 + VACUUM(ANALYZE). 다음은 mixed"
}

# ---------------------------------------------------------------- mixed
LONG_ACC=""
long_account() {
  LONG_ACC=$(XQ "<long-account>" "$PY -c 'import json;a=json.load(open(\"$W/results/inventory.json\"))[\"accounts\"];print(max(a,key=lambda x:(len(x[\"dates\"]),x[\"account_id\"]))[\"account_id\"])'")
}
k6_mixed() {  # k6_mixed <단계 이름>
  local st=$1
  detach tools "$st" "mkdir -p $OUTD && cd $S5DIR && K6_CSV_TIME_FORMAT=unix_milli $K6_BIN run --quiet --no-color --tag stage=$st \
    --summary-export $OUTD/$st.summary.json --out csv=$OUTD/$st.csv.gz -e WARM=$MIXED_WARM -e BASE_URL=$API_URL \
    -e TOKENS=$OUTD/tokens.json -e PAYLOADS=$OUTD/payloads-$st.json -e DEVICES=$OUTD/devices.json -e CONTEXTS=$OUTD/contexts.json \
    -e WORKLOAD=$S5DIR/$WORKLOAD -e TOTAL_RPS=$MIXED_TOTAL -e DURATION_SEC=$MIXED_SEC $S5DIR/k6-mixed-read-upload.js"
  local rc; rc=$(wait_rc "$st")
  log "$st k6 종료 코드 $rc(99 = threshold abortOnFail)"
}
step_mixed() {
  local k st wrate
  long_account
  consumer_set "$CONSUMERS"
  XC tools "mkdir -p $OUTD; if [ ! -f $OUTD/tokens.json ]; then $(P tokens --inventory "$W/results/inventory.json" --out "$OUTD/tokens.json" --exclude "$LONG_ACC"); fi"
  XC tools "$(P payloads-mixed --out-dir "$OUTD" --workload "$S5DIR/$WORKLOAD" --total "$MIXED_TOTAL" --duration "$MIXED_SEC" --runs "$MIXED_RUNS" --exclude "$LONG_ACC")"
  wrate=$(XQ 135.7 "$PY -c \"import json;print(json.load(open('$OUTD/mixed-plan.json'))['write_rps'])\"")
  for k in $(seq 1 "$MIXED_RUNS"); do
    st="mixed-r$k"
    fits $((MIXED_SEC + MIXED_GRACE + 300)) "$st" || break
    XC tools "$(R rec "before-$st")"
    log "$st: ${MIXED_TOTAL} rps × ${MIXED_SEC}s(업로드 ${wrate}/s), consumer ${CONSUMERS}, 파티션 ${PARTITIONS}"
    XC tools "rm -f $OUTD/$st.stop; date +%s > $OUTD/$st.start"
    detach tools "watch-$st" "$(P backlog-watch "$st" --dir "$OUTD" --rate "$wrate" --start-file "$OUTD/$st.start" --stop-file "$OUTD/$st.stop" --duration "$MIXED_SEC")"
    k6_mixed "$st"
    [[ "$DRY_RUN" == 1 ]] || sleep "$MIXED_GRACE"
    XC tools "touch $OUTD/$st.stop"
    wait_rc "watch-$st" > /dev/null
    XC tools "$(R rec "after-$st")"
    XC tools "$(P report-mixed "$st" --dir "$OUTD" --workload "$S5DIR/$WORKLOAD" --total "$MIXED_TOTAL" --duration "$MIXED_SEC" --warm "$MIXED_WARM" --consumers "$CONSUMERS")" \
      || log "$st 판정 실패(분석 오류)"
  done
}

step_final() { XC tools "$(R final)" || log "final 확인 실패(a-final.txt·a-run.log)"; }
step_post_dump() {
  local rc
  detach pg post-dump "$(R post-dump)"; rc=$(wait_rc post-dump)
  [[ "$rc" == 0 ]] || log "post-dump 종료 코드 $rc"
}
step_fetch() {
  local name pod_sha local_sha
  name="step7a-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  XC tools "tar czf $W/$name -C $W --exclude='k6out/payloads-*.json' --exclude='k6out/payloads-*.rows.json' --exclude='k6out/*tokens.json' --exclude='k6out/devices.json' k6out results"
  pod_sha=$(XQ "<sha>" "cd $W && (sha256sum $name 2>/dev/null || shasum -a 256 $name) | awk '{print \$1}'")
  if [[ "$LOCAL" == 1 ]]; then [[ "$DRY_RUN" == 1 ]] || cp "$W/$name" "$STATE/$name"
  else K cp "$NS/$POD:/work/$name" "$STATE/$name" -c tools; fi
  if [[ "$DRY_RUN" == 1 ]]; then local_sha="<sha>"; else local_sha=$(sha "$STATE/$name"); fi
  [[ "$local_sha" == "$pod_sha" ]] || die "회수본 sha256 불일치(원본 $pod_sha, 로컬 $local_sha)"
  log "회수 $STATE/$name sha256 $local_sha"
}
step_fetch_dump() {
  local pod_sha local_sha
  pod_sha=$(XQ "<sha>" "awk '{print \$1}' $W/post-7a.dump.sha256")
  if [[ "$LOCAL" == 1 ]]; then [[ "$DRY_RUN" == 1 ]] || cp "$W/post-7a.dump" "$STATE/post-7a.dump"
  else K cp "$NS/$POD:/work/post-7a.dump" "$STATE/post-7a.dump" -c tools; fi
  if [[ "$DRY_RUN" == 1 ]]; then local_sha="<sha>"; else local_sha=$(sha "$STATE/post-7a.dump"); fi
  [[ "$local_sha" == "$pod_sha" ]] || die "측정 직후 덤프 sha256 불일치(원본 $pod_sha, 로컬 $local_sha)"
  echo "$local_sha  post-7a.dump" > "$STATE/post-7a.dump.sha256"
  log "회수 $STATE/post-7a.dump sha256 $local_sha"
}

case "${1:-}" in
  check) step_check ;;
  push) step_push ;;
  prep) step_check; step_push; step_prep ;;
  mixed) trap 'consumer_restore' EXIT; step_check; step_mixed ;;
  final) step_final; consumer_restore ;;
  post-dump) step_post_dump ;;
  fetch) step_fetch ;;
  fetch-dump) step_fetch_dump ;;
  all)
    trap 'consumer_restore; step_fetch || log "회수 실패"' EXIT
    step_check; step_push; step_prep; step_mixed; step_final
    [[ "$POST_DUMP" == 1 ]] && step_post_dump
    ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac

#!/usr/bin/env bash
# 측정 Pod 조작(Mac에서 실행). 측정 당일과 랩 리허설이 같은 코드를 쓴다.
#
#   사용: measurement-pod.sh <명령>
#     up             manifest.json의 app_image(uri@digest)로 step5-tools Pod 생성 → Running 대기 → 노드 확인(loadgen, API와 다른 노드)
#     push [--no-dump]  측정 도구(하네스·재배치 SQL)·덤프·alembic head를 /work로 kubectl cp. 덤프 sha256 로컬=Pod 비교
#                    --no-dump: 덤프만 빼고(데이터가 이미 RDS에 있을 때) 나머지와 alembic head는 넣는다
#     consumer-down  mosemo-consumer 원래 replicas 기록 → 0
#     consumer-up    기록한 replicas로 되돌림 → Available 대기
#     fetch          Pod 안 결과를 묶어 kubectl cp로 회수, sha256 비교
#     down           step5-tools 삭제
#
# 환경 변수
#   MEASUREMENT_KUBECONFIG  기본 ~/.mosemo/lab/measurement.kubeconfig. KUBECONFIG는 이것만 쓴다
#   MANIFEST                기본 ~/.mosemo/lab/measurement/manifest.json(app_image.uri·digest)
#   LOADGEN_NODE            "준비 완료" 신호의 loadgen 노드 이름(노드 조회 권한이 없어서 이 이름으로 확인)
#   DUMP, ALEMBIC_FILE      덤프와 그 head 파일(기본 ~/mosemo-step5/seed.dump, ~/mosemo-step5/s1-alembic.txt)
#   STATE                   로컬 기록 폴더(기본 ~/mosemo-step5/kube)
#   DRY_RUN=1               클러스터에 닿지 않고 실행할 명령만 출력
#   MEASUREMENT=b           측정 B: Pod 정의를 measurement-pod-b.yaml(batch 컨테이너 추가)로, push에 dense_day_accounts.py를 더한다
#
# 하지 않는 것(브리핑 "하지 말 것"): terraform·aws CLI·session·deploy 스크립트, step5-tools 외 Pod 생성,
# mosemo-consumer 외 Deployment 변경, Kafka 접속. 이 스크립트가 부르는 kubectl은 아래 K 함수뿐이다.
set -euo pipefail

ART="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEASUREMENT_KUBECONFIG="${MEASUREMENT_KUBECONFIG:-$HOME/.mosemo/lab/measurement.kubeconfig}"
MANIFEST="${MANIFEST:-$HOME/.mosemo/lab/measurement/manifest.json}"
NS=mosemo
POD=step5-tools
CONSUMER=mosemo-consumer
DUMP="${DUMP:-$HOME/mosemo-step5/seed.dump}"
ALEMBIC_FILE="${ALEMBIC_FILE:-$HOME/mosemo-step5/s1-alembic.txt}"
STATE="${STATE:-$HOME/mosemo-step5/kube}"
DRY_RUN="${DRY_RUN:-0}"
LOADGEN_NODE="${LOADGEN_NODE:-}"
TOOLS=(db_harness.py
       reorder-segments-by-time.sql)
MEASUREMENT="${MEASUREMENT:-a}"
POD_YAML=measurement-pod.yaml
case "$MEASUREMENT" in
  a) ;;
  b) POD_YAML=measurement-pod-b.yaml; TOOLS+=(dense_day_accounts.py) ;;
  *) echo "MEASUREMENT는 a 또는 b" >&2; exit 2 ;;
esac

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
log() { echo "[$(utc)] $*" | tee -a "$STATE/kube.log" >&2; }
die() { log "실패: $*"; exit 1; }

# kubectl 호출은 이 함수 하나로만 한다(KUBECONFIG 고정, 네임스페이스 고정). DRY_RUN이면 출력만.
K() {
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ KUBECONFIG=$MEASUREMENT_KUBECONFIG kubectl -n $NS $*" >&2; return 0; fi
  KUBECONFIG="$MEASUREMENT_KUBECONFIG" kubectl -n "$NS" "$@"
}
# 출력이 필요한 kubectl. DRY_RUN이면 자리 값($1)을 돌려준다.
KQ() {
  local dry=$1; shift
  if [[ "$DRY_RUN" == 1 ]]; then echo "+ KUBECONFIG=$MEASUREMENT_KUBECONFIG kubectl -n $NS $*" >&2; echo "$dry"; return 0; fi
  KUBECONFIG="$MEASUREMENT_KUBECONFIG" kubectl -n "$NS" "$@"
}
X() { K exec "$POD" -c "$1" -- "${@:2}"; }            # Pod 안 명령
XQ() { local dry=$1; shift; KQ "$dry" exec "$POD" -c "$1" -- "${@:2}"; }
L() { if [[ "$DRY_RUN" == 1 ]]; then echo "+ (로컬) $*" >&2; else "$@"; fi; }   # 로컬 명령
sha() { shasum -a 256 "$1" | awk '{print $1}'; }

preflight() {
  [[ "$DRY_RUN" == 1 ]] && return 0
  [[ -f "$MEASUREMENT_KUBECONFIG" ]] || die "kubeconfig 없음: $MEASUREMENT_KUBECONFIG"
  command -v kubectl >/dev/null || die "kubectl 없음"
}

image_ref() {
  if [[ "$DRY_RUN" == 1 && ! -f "$MANIFEST" ]]; then echo "<app_image.uri>@<digest>"; return 0; fi
  [[ -f "$MANIFEST" ]] || die "매니페스트 없음: $MANIFEST"
  python3 - "$MANIFEST" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))["app_image"]
uri, digest = m["uri"], m["digest"]
if not digest.startswith("sha256:"):
    digest = "sha256:" + digest
base = uri.split("@", 1)[0]
print(f"{base}@{digest}")
EOF
}

# 매니페스트 안에서 이 노드 이름이 들어 있는 객체에 "loadgen"이 함께 있는지(노드 조회 권한이 없을 때의 대조)
manifest_says_loadgen() {
  [[ -f "$MANIFEST" ]] || return 1
  python3 - "$MANIFEST" "$1" <<'EOF'
import json, sys
node = sys.argv[2]
def walk(o):
    if isinstance(o, dict):
        if node in json.dumps(o) and "loadgen" in json.dumps(o) and len(json.dumps(o)) < 4000:
            yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)
sys.exit(0 if any(True for _ in walk(json.load(open(sys.argv[1])))) else 1)
EOF
}

# --------------------------------------------------------------------------- up
cmd_up() {
  preflight
  local img node api_nodes pool phase
  img=$(image_ref)
  log "up: 이미지 $img"
  if [[ "$DRY_RUN" != 1 ]] && K get pod "$POD" >/dev/null 2>&1; then die "$POD가 이미 있다(down 먼저)"; fi
  sed "s|<APP_IMAGE>|$img|" "$ART/$POD_YAML" > "$STATE/pod.rendered.yaml"
  grep -q "<APP_IMAGE>" "$STATE/pod.rendered.yaml" && die "이미지 자리를 채우지 못했다"
  K create -f "$STATE/pod.rendered.yaml"
  K wait --for=condition=Ready "pod/$POD" --timeout=600s || die "Running/Ready 대기 시간 초과"
  phase=$(KQ Running get pod "$POD" -o 'jsonpath={.status.phase}')
  node=$(KQ "<loadgen-node>" get pod "$POD" -o 'jsonpath={.spec.nodeName}')
  api_nodes=$(KQ "<app-node>" get pods -l app=mosemo-server -o 'jsonpath={.items[*].spec.nodeName}')
  [[ "$phase" == Running ]] || die "phase=$phase"
  # loadgen 확인: 노드 라벨을 읽을 수 있으면 그것으로, 없으면(권한 없음) 신호의 노드 이름·매니페스트와 대조
  pool=""
  if [[ "$DRY_RUN" == 1 ]]; then
    echo "+ KUBECONFIG=$MEASUREMENT_KUBECONFIG kubectl get node $node -o jsonpath={.metadata.labels.pool}  (권한 없으면 다음 대조로)" >&2
  else
    pool=$(KUBECONFIG="$MEASUREMENT_KUBECONFIG" kubectl get node "$node" -o 'jsonpath={.metadata.labels.pool}' 2>/dev/null || true)
  fi
  local how
  if [[ "$pool" == loadgen ]]; then how="노드 라벨 pool=loadgen"
  elif [[ -n "$LOADGEN_NODE" && "$node" == "$LOADGEN_NODE" ]]; then how="신호의 loadgen 노드 이름과 일치"
  elif [[ "$DRY_RUN" != 1 ]] && manifest_says_loadgen "$node"; then how="매니페스트의 loadgen 항목과 일치"
  elif [[ "$DRY_RUN" == 1 ]]; then how="(DRY_RUN)"
  else die "노드 $node가 loadgen인지 확인하지 못했다(LOADGEN_NODE를 주거나 매니페스트 확인)"; fi
  case " $api_nodes " in *" $node "*) die "측정 Pod 노드 $node가 API Pod 노드($api_nodes)와 같다";; esac
  echo "pod_node=$node api_nodes=$api_nodes loadgen_check=$how image=$img" > "$STATE/up.txt"
  log "up: $POD Running, 노드 $node($how), API 노드 $api_nodes"
}

# --------------------------------------------------------------------------- push
cmd_push() {
  preflight
  local f local_sha pod_sha no_dump=0
  [[ "${1:-}" == --no-dump ]] && no_dump=1   # 재개 실행: 데이터가 이미 RDS에 있다. alembic head 파일은 넣는다
  X tools mkdir -p /work/step5 /work/results
  for f in "${TOOLS[@]}"; do K cp "$ART/$f" "$NS/$POD:/work/step5/$f" -c tools; done
  [[ "$DRY_RUN" == 1 || $no_dump == 1 || -f "$DUMP" ]] || die "덤프 없음: $DUMP"
  [[ "$DRY_RUN" == 1 || -f "$ALEMBIC_FILE" ]] || die "alembic head 파일 없음: $ALEMBIC_FILE"
  K cp "$ALEMBIC_FILE" "$NS/$POD:/work/s1-alembic.txt" -c tools
  if [[ $no_dump == 1 ]]; then
    echo "dump=(--no-dump)" > "$STATE/push.txt"
    log "push: 도구 ${#TOOLS[@]}개, alembic head, 덤프는 넣지 않음(--no-dump)"
    return 0
  fi
  log "push: 덤프 $DUMP 복사 시작"
  K cp "$DUMP" "$NS/$POD:/work/seed.dump" -c tools
  if [[ "$DRY_RUN" == 1 ]]; then local_sha="<local-sha256>"; else local_sha=$(sha "$DUMP"); fi
  pod_sha=$(XQ "<local-sha256>" tools sha256sum /work/seed.dump | awk '{print $1}')
  [[ "$local_sha" == "$pod_sha" ]] || die "덤프 sha256 불일치(로컬 $local_sha, Pod $pod_sha)"
  echo "dump=$DUMP sha256=$local_sha pod_sha256=$pod_sha" > "$STATE/push.txt"
  log "push: 도구 ${#TOOLS[@]}개, 덤프 sha256 일치 $local_sha"
}

# --------------------------------------------------------------------------- consumer
cmd_consumer_down() {
  preflight
  local n
  if [[ -s "$STATE/consumer-replicas.txt" ]]; then die "이미 기록이 있다($(cat "$STATE/consumer-replicas.txt")). consumer-up을 먼저"; fi
  n=$(KQ 1 get deploy "$CONSUMER" -o 'jsonpath={.spec.replicas}')
  [[ "$n" =~ ^[0-9]+$ ]] || die "replicas 읽기 실패: $n"
  echo "$n" > "$STATE/consumer-replicas.txt"
  K scale "deploy/$CONSUMER" --replicas=0
  log "consumer-down: 원래 replicas $n 기록 → 0"
}

cmd_consumer_up() {
  preflight
  [[ -s "$STATE/consumer-replicas.txt" ]] || die "기록한 replicas가 없다(consumer-down을 하지 않았음)"
  local n avail
  n=$(cat "$STATE/consumer-replicas.txt")
  K scale "deploy/$CONSUMER" --replicas="$n"
  for _ in $(seq 60); do   # rollout status는 watch 권한이 필요할 수 있어 get으로 기다린다
    avail=$(KQ "$n" get deploy "$CONSUMER" -o 'jsonpath={.status.availableReplicas}')
    [[ "${avail:-0}" == "$n" ]] && break
    [[ "$DRY_RUN" == 1 ]] || sleep 5
  done
  [[ "${avail:-0}" == "$n" ]] || die "consumer Available $avail/$n(5분 대기)"
  mv "$STATE/consumer-replicas.txt" "$STATE/consumer-replicas.restored.$(date -u +%Y%m%dT%H%M%SZ).txt"
  log "consumer-up: replicas $n, Available $avail"
}

# --------------------------------------------------------------------------- fetch / down
cmd_fetch() {
  preflight
  local name pod_sha local_sha
  name="results-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  X tools tar czf "/work/$name" -C /work results
  pod_sha=$(XQ "<sha>" tools sha256sum "/work/$name" | awk '{print $1}')
  K cp "$NS/$POD:/work/$name" "$STATE/$name" -c tools
  if [[ "$DRY_RUN" == 1 ]]; then local_sha="<sha>"; else local_sha=$(sha "$STATE/$name"); fi
  [[ "$local_sha" == "$pod_sha" ]] || die "회수본 sha256 불일치(Pod $pod_sha, 로컬 $local_sha)"
  log "fetch: $STATE/$name sha256 $local_sha"
}

cmd_down() {
  preflight
  K delete pod "$POD" --ignore-not-found --wait=true --timeout=180s
  log "down: $POD 삭제"
}

case "${1:-}" in
  up) cmd_up ;;
  push) cmd_push "${2:-}" ;;
  consumer-down) cmd_consumer_down ;;
  consumer-up) cmd_consumer_up ;;
  fetch) cmd_fetch ;;
  down) cmd_down ;;
  *) sed -n '2,22p' "$0"; exit 2 ;;
esac

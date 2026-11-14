#!/usr/bin/env bash
# Offline harness for the session scripts. Fake aws, kubectl, terraform and docker (plus git, curl and dig,
# so no real repository state or network is used) are put first on PATH; each call is only recorded in
# $FAKE_LOG as "<command>|TF_VAR_measurement=<value>|<arguments>" and answered from canned data.
# Nothing reaches AWS, Cloudflare or a cluster. Usage: bash infra/deployment/tests/run.sh
set -euo pipefail

DEPLOY_DIR=$(cd "$(dirname "$0")/.." && pwd -P)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
FAKE_BIN="$WORK/bin"
mkdir -p "$FAKE_BIN"

cat >"$FAKE_BIN/fake" <<'FAKE'
#!/bin/bash
# Records the call, then answers like the real tool would for the session scripts.
cmd=$(basename "$0")
printf '%s|TF_VAR_measurement=%s|%s\n' "$cmd" "${TF_VAR_measurement-unset}" "$*" >>"$FAKE_LOG"
args=" $* "
state="$FAKE_STATE"
case $cmd in
  terraform)
    case $args in
      *" state list "*) cat "$state/tfstate" 2>/dev/null || true ;;
      *" output -raw ecr_repository_url "*) echo "123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/mosemo-lab-server" ;;
      *" output -raw alb_dns_name "*) echo "mosemo-lab-1.ap-southeast-2.elb.amazonaws.com" ;;
      *" output -json database "*) echo '{"DB_HOST":"mosemo-lab.abc123.ap-southeast-2.rds.amazonaws.com"}' ;;
      *" show -json "*) cat "$state/plan.json" ;;
      *" plan "*) exit "$(cat "$state/plan_exit" 2>/dev/null || echo 0)" ;;
      *" destroy "*) : >"$state/tfstate" ;;
    esac
    ;;
  aws)
    # A canned answer in $FAKE_STATE/aws.<service>.<operation> (exit status in <file>.exit) wins.
    if [[ -f $state/aws.$1.$2 ]]; then
      cat "$state/aws.$1.$2"
      exit "$(cat "$state/aws.$1.$2.exit" 2>/dev/null || echo 0)"
    fi
    case $args in
      *" sts get-caller-identity "*) echo "arn:aws:sts::123456789012:assumed-role/MosemoInfrastructureRole/test" ;;
      *" eks update-kubeconfig "*)
        out=${args#* --kubeconfig }
        echo "apiVersion: v1" >"${out%% *}"
        ;;
      *" rds describe-db-snapshots "*)
        echo "An error occurred (DBSnapshotNotFound) when calling the DescribeDBSnapshots operation" >&2
        exit 254
        ;;
      *" rds describe-db-instances "*) echo False ;;
      *" elbv2 describe-load-balancers "*) echo "arn:aws:elasticloadbalancing:ap-southeast-2:123456789012:loadbalancer/app/mosemo-lab/abc" ;;
      *" elbv2 describe-load-balancer-attributes "*) echo false ;;
      *" ecr describe-images "*) echo "sha256:$(printf 'a%.0s' {1..64})" ;;
    esac
    ;;
  kubectl)
    case $args in
      *" config current-context "*) echo "arn:aws:eks:ap-southeast-2:123456789012:cluster/mosemo-lab" ;;
      *" config view "*server*) echo "https://example.eks.amazonaws.com" ;;
      *" config view "*certificate-authority-data*) echo "Q0EK" ;;
      *" create token "*)
        python3 -c '
import base64, json, sys
part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
print(part({"alg": "none"}) + "." + part({"exp": int(sys.argv[1])}) + ".sig")' "$(cat "$state/token_exp")"
        ;;
      *" get nodes -l pool=loadgen "*) cat "$state/loadgen_nodes" ;;
      *" get nodes -l pool=app "*) cat "$state/app_nodes" ;;
      *" get pods -l app=mosemo-"*deletionTimestamp*) cat "$state/pod_nodes" ;;
      *" get nodes -o jsonpath="*allocatable*) cat "$state/nodes" ;;
      *" get nodes --no-headers "*) cat "$state/nodes" ;;
      *" get nodes -o json "*) cat "$state/nodes.json" ;;
      *" get pods -A -o json "* | *" get pods -l app=mosemo-server -o json "*) echo '{"items": []}' ;;
      *" get pods -l app=mosemo-server -o jsonpath="*) echo node-app ;;
      *" get limitrange "*) echo '{"spec": {"limits": [{"default": {"cpu": "1"}}]}}' ;;
      *" apply -f - "*) cat >/dev/null ;;
      *" get pod step5-"*"status.phase"*) echo Succeeded ;;
      *" get pod step5-"*" -o json "*)
        echo '{"spec": {"nodeName": "node-loadgen"}, "status": {"containerStatuses": [{"imageID": "postgres@sha256:x"}]}}'
        ;;
      *" logs step5-alembic "*) echo "a3b4c5d6e7f8 (head)" ;;
      *" logs step5-ready "*) printf 'create_extension=yes\nrds_5432=yes\napi_http=200\n' ;;
      *" get deployment "*'"Available"'*) echo True ;;
      *"ENABLE_PREFIX_DELEGATION"*) echo true ;;
      *" daemonset ebs-csi-node -o jsonpath="*) wc -l <"$state/nodes" | tr -d ' ' ;;
      *" get pvc "*) echo Bound ;;
      *" get pod mosemo-kafka-0 "*) echo node-kafka ;;
      *" get node node-kafka "*) echo kafka ;;
      *" get job "*"Complete"*) echo True ;;
      *" get job "*"Failed"*) echo "" ;;
      *" get job "*) exit 1 ;;
      *" set image --local "*)
        file=${args#* -f }
        cat "${file%% *}"
        ;;
      *" get deployment "*"containers[0].image"*) cat "$state/image" ;;
      *" exec mosemo-kafka-0 "*) echo "GROUP mosemo.activity-batches Stable" ;;
      *" logs deployment/mosemo-batch "*) echo '{"operation":"label-proposals","outcome":"success"}' ;;
    esac
    ;;
  git)
    case $args in
      *" status --porcelain "*) : ;;
      *" rev-parse HEAD "*) printf 'b%.0s' {1..40}; echo ;;
    esac
    ;;
  curl)
    case $args in
      *bundle.pem*" -o "*) out=${args#* -o }; echo "-----BEGIN CERTIFICATE-----" >"${out%% *}" ;;
      *"https://mosemo-api.danuh.com/openapi.json"*) printf 200 ;;
      *"/api/v1/accounts/me"*) printf 401 ;;
      *"http://mosemo-api.danuh.com/openapi.json"*) printf 301 ;;
    esac
    ;;
  dig) echo "mosemo-lab-1.ap-southeast-2.elb.amazonaws.com." ;;
esac
exit 0
FAKE
chmod +x "$FAKE_BIN/fake"
for tool in aws kubectl terraform docker git curl dig; do
  ln -s fake "$FAKE_BIN/$tool"
done
export PATH="$FAKE_BIN:$PATH"
export CLOUDFLARE_API_TOKEN=fake-token-for-tests

passed=0
failed=0
ok() {
  printf 'ok   %s\n' "$1"
  passed=$((passed + 1))
}
not_ok() {
  printf 'FAIL %s\n' "$1"
  [[ -n ${2:-} ]] && tail -n 15 <<<"$2" | sed 's/^/     /'
  failed=$((failed + 1))
}

# New session directory (0700, secrets.env 0600) and fake state for one scenario.
new_case() {
  CASE="$WORK/case$((passed + failed))-$RANDOM"
  export MOSEMO_SESSION_DIR="$CASE/session"
  export FAKE_STATE="$CASE/fake"
  export FAKE_LOG="$CASE/calls.log"
  mkdir -p "$MOSEMO_SESSION_DIR" "$FAKE_STATE"
  chmod 700 "$MOSEMO_SESSION_DIR"
  printf '%s=test-value\n' DB_PASSWORD AUTH_JWT_SECRET_KEY KAKAO_REST_API_KEY KAKAO_CLIENT_SECRET GOOGLE_API_KEY \
    >"$MOSEMO_SESSION_DIR/secrets.env"
  chmod 600 "$MOSEMO_SESSION_DIR/secrets.env"
  : >"$FAKE_LOG"
  : >"$FAKE_STATE/tfstate"
  printf 'node-kafka kafka 110 1900000Ki\nnode-app app 110 1900000Ki\n' >"$FAKE_STATE/nodes"
  echo node-loadgen >"$FAKE_STATE/loadgen_nodes"
  printf 'node-app-%s\n' 4 2 3 1 >"$FAKE_STATE/app_nodes"
  # "<deletionTimestamp> <node>": one Pod per app node, plus a terminating one the check must ignore.
  printf ' node-app-%s\n' 1 2 3 4 >"$FAKE_STATE/pod_nodes"
  echo "2026-11-20T09:00:00Z node-app-1" >>"$FAKE_STATE/pod_nodes"
  echo $(($(date +%s) + 14 * 3600)) >"$FAKE_STATE/token_exp"
  echo "123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/mosemo-lab-server@sha256:$(printf 'a%.0s' {1..64})" >"$FAKE_STATE/image"
}
profile() { echo "$1" >"$MOSEMO_SESSION_DIR/profile"; }
kubeconfig() { echo "apiVersion: v1" >"$MOSEMO_SESSION_DIR/kubeconfig"; }
run() { # run SCRIPT ARGS... ; sets STATUS and OUTPUT
  set +e
  OUTPUT=$(bash "$DEPLOY_DIR/$1" "${@:2}" 2>&1 </dev/null)
  STATUS=$?
  set -e
}
logged() { grep -E "$1" "$FAKE_LOG" >/dev/null; }
# The rendered kafka-init Job is the one applied, and only its partition target differs from the source.
rendered_partitions() {
  local rendered="$MOSEMO_SESSION_DIR/rendered/kafka-init-job.yaml"
  logged "^kubectl\\|.*apply -f $rendered" &&
    [[ $(diff "$DEPLOY_DIR/../kubernetes/kafka-init-job.yaml" "$rendered" | grep -c '^[<>]') -eq $(($1 == 4 ? 0 : 2)) ]] &&
    grep -A1 'name: KAFKA_TOPIC_PARTITIONS' "$rendered" | grep -q "value: \"$1\""
}

echo "a. control run calls the fakes"
new_case
run session-up.sh
if ((STATUS == 0)) && logged '^terraform\|.*\| *-chdir=.* apply' && logged '^kubectl\|' && logged '^aws\|'; then
  ok "a session-up completes against the fakes"
else
  not_ok "a session-up completes against the fakes (exit $STATUS)" "$OUTPUT"
fi
if rendered_partitions 4; then
  ok "a default profile applies the kafka-init Job rendered with 4 partitions"
else
  not_ok "a default profile applies the kafka-init Job rendered with 4 partitions" "$OUTPUT"
fi
new_case
kubeconfig
run deploy.sh
if ((STATUS == 0)) && logged 'apply -f .*/batch-deployment.yaml'; then
  ok "a deploy (default profile) rolls out batch"
else
  not_ok "a deploy (default profile) rolls out batch (exit $STATUS)" "$OUTPUT"
fi
# rendered_workload FILE REPLICAS SPREAD_APP|none
rendered_workload() {
  local rendered="$MOSEMO_SESSION_DIR/rendered/$1"
  grep -qx "  replicas: $2" "$rendered" || return 1
  if [[ $3 == none ]]; then
    [[ $(grep -c topologySpreadConstraints "$rendered") -eq 0 ]]
  else
    python3 - "$rendered" "$3" <<'PY'
import sys
text, app = open(sys.argv[1]).read(), sys.argv[2]
assert text.count("topologySpreadConstraints:") == 1
assert ("      topologySpreadConstraints:\n      - maxSkew: 1\n        topologyKey: kubernetes.io/hostname\n"
        "        whenUnsatisfiable: DoNotSchedule\n        nodeTaintsPolicy: Honor\n        labelSelector:\n"
        "          matchLabels:\n            app: " + app + "\n") in text
PY
  fi
}
if rendered_workload deployment.yaml 1 none && rendered_workload consumer-deployment.yaml 1 none && ! logged 'get nodes -l pool=app'; then
  ok "a default profile renders API and consumer with 1 replica, no spread, no placement check"
else
  not_ok "a default profile renders API and consumer with 1 replica, no spread, no placement check" "$OUTPUT"
fi

echo "b. measurement lock blocks every changing script before any call"
for script in "session-up.sh" "deploy.sh" "session-down.sh" "measurement-ready.sh a3b4c5d6e7f8"; do
  new_case
  kubeconfig
  profile measurement
  echo "started_at=2026-11-20T09:00:00Z" >"$MOSEMO_SESSION_DIR/measurement.lock"
  # shellcheck disable=SC2086 # Script name and its argument.
  run $script
  if ((STATUS != 0)) && grep -q "measurement lock" <<<"$OUTPUT" && [[ ! -s $FAKE_LOG ]]; then
    ok "b $script refuses with the lock and calls nothing"
  else
    not_ok "b $script refuses with the lock and calls nothing (exit $STATUS, $(wc -l <"$FAKE_LOG" | tr -d ' ') calls)" "$OUTPUT"
  fi
done

echo "c. measurement profile reaches terraform as TF_VAR_measurement=true"
new_case
profile measurement
printf 'node-kafka kafka 110 1900000Ki\nnode-app app 110 1900000Ki\nnode-loadgen loadgen 110 1900000Ki\n' >"$FAKE_STATE/nodes"
run session-up.sh
if ((STATUS == 0)) &&
  ! grep '^terraform|' "$FAKE_LOG" | grep -v '^terraform|TF_VAR_measurement=true|' >/dev/null &&
  logged '^terraform\|TF_VAR_measurement=true\|.* state list' &&
  logged '^terraform\|TF_VAR_measurement=true\|.* output -json database' &&
  logged '^terraform\|TF_VAR_measurement=true\|.* plan .*-detailed-exitcode' &&
  logged '^kubectl\|.*wait .*pool=loadgen' &&
  logged 'apply -f .*/measurement-rbac.yaml' &&
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["profile"]=="measurement" and d["plan_exit_code"]==0' \
    "$MOSEMO_SESSION_DIR/measurement/terraform.json"; then
  ok "c every terraform call carries TF_VAR_measurement=true; terraform.json recorded"
else
  not_ok "c every terraform call carries TF_VAR_measurement=true (exit $STATUS)" "$OUTPUT"
fi
if rendered_partitions 8; then
  ok "c measurement profile applies the kafka-init Job rendered with 8 partitions"
else
  not_ok "c measurement profile applies the kafka-init Job rendered with 8 partitions" "$OUTPUT"
fi
new_case
profile measurement
echo 2 >"$FAKE_STATE/plan_exit"
printf 'node-kafka kafka 110 1900000Ki\nnode-app app 110 1900000Ki\nnode-loadgen loadgen 110 1900000Ki\n' >"$FAKE_STATE/nodes"
run session-up.sh
if ((STATUS != 0)) && grep -q "plan after apply exited 2" <<<"$OUTPUT" &&
  python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["plan_exit_code"]==2' "$MOSEMO_SESSION_DIR/measurement/terraform.json"; then
  ok "c drift after apply fails session-up and is recorded"
else
  not_ok "c drift after apply fails session-up and is recorded (exit $STATUS)" "$OUTPUT"
fi
new_case
printf 'node-kafka kafka 110 1900000Ki\nnode-app app 110 1200Mi\n' >"$FAKE_STATE/nodes"
run session-up.sh
if ((STATUS != 0)) && grep -q "allocatable memory too low" <<<"$OUTPUT"; then
  ok "c app node below 1236Mi allocatable stops session-up"
else
  not_ok "c app node below 1236Mi allocatable stops session-up (exit $STATUS)" "$OUTPUT"
fi

echo "d. measurement profile deploy drops batch"
new_case
kubeconfig
profile measurement
run deploy.sh
if ((STATUS == 0)) && ! logged 'batch-deployment.yaml' && logged '^kubectl\|.*delete deployment mosemo-batch' &&
  ! logged 'logs deployment/mosemo-batch'; then
  ok "d no batch apply, existing mosemo-batch deleted, no batch smoke"
else
  not_ok "d no batch apply, existing mosemo-batch deleted, no batch smoke (exit $STATUS)" "$OUTPUT"
fi
if rendered_workload deployment.yaml 4 mosemo-server && rendered_workload consumer-deployment.yaml 4 mosemo-consumer &&
  grep -q "one mosemo-server and one mosemo-consumer Pod on each app node" <<<"$OUTPUT"; then
  ok "d API and consumer rendered with 4 replicas and the hostname spread; placement checked"
else
  not_ok "d API and consumer rendered with 4 replicas and the hostname spread; placement checked" "$OUTPUT"
fi
for pods in "1 2 3" "1 1 2 3" "1 2 3 4 5"; do
  new_case
  kubeconfig
  profile measurement
  printf 'node-app-%s\n' 1 2 3 4 >"$FAKE_STATE/app_nodes"
  # shellcheck disable=SC2086 # Word-split node suffixes.
  printf ' node-app-%s\n' $pods >"$FAKE_STATE/pod_nodes"
  run deploy.sh
  if ((STATUS != 0)) && grep -q "Pods are not one per app node" <<<"$OUTPUT" && ! grep -q "Smoke check" <<<"$OUTPUT"; then
    ok "d Pods on app nodes [$pods] fail the placement check before the smoke check"
  else
    not_ok "d Pods on app nodes [$pods] fail the placement check before the smoke check (exit $STATUS)" "$OUTPUT"
  fi
done

echo "e. session-down rerun guard"
plan_json() { # plan_json ALB_AFTER_JSON
  cat >"$FAKE_STATE/plan.json" <<EOF
{"resource_changes": [
  {"address": "aws_db_instance.main", "change": {"actions": ["update"],
   "before": {"skip_final_snapshot": false}, "after": {"skip_final_snapshot": true}, "after_unknown": {}}},
  {"address": "aws_lb.api", "change": {"actions": ["update"],
   "before": {"enable_deletion_protection": false, "idle_timeout": 60}, "after": $1, "after_unknown": {}}},
  {"address": "aws_vpc.main", "change": {"actions": ["no-op"], "before": {}, "after": {}, "after_unknown": {}}}
]}
EOF
}
new_case
printf 'aws_db_instance.main\naws_lb.api\n' >"$FAKE_STATE/tfstate"
plan_json '{"enable_deletion_protection": true, "idle_timeout": 60}'
run session-down.sh
if ((STATUS == 0)) && logged '^terraform\|.* apply -input=false .*skip-final-snapshot.tfplan' &&
  logged 'modify-load-balancer-attributes .*deletion_protection.enabled,Value=false' && logged '^terraform\|.* destroy '; then
  ok "e RDS update + ALB enable_deletion_protection only: guard passes, ALB protection lifted, destroy runs"
else
  not_ok "e RDS update + ALB enable_deletion_protection only: guard passes (exit $STATUS)" "$OUTPUT"
fi
new_case
printf 'aws_db_instance.main\naws_lb.api\n' >"$FAKE_STATE/tfstate"
plan_json '{"enable_deletion_protection": true, "idle_timeout": 120}'
run session-down.sh
if ((STATUS != 0)) && grep -q "aws_lb.api:update(enable_deletion_protection,idle_timeout)" <<<"$OUTPUT" &&
  ! logged ' apply -input=false' && ! logged ' destroy '; then
  ok "e another ALB attribute change fails the guard before any apply"
else
  not_ok "e another ALB attribute change fails the guard before any apply (exit $STATUS)" "$OUTPUT"
fi

echo "f. measurement-lock"
new_case
kubeconfig
profile measurement
end_utc=$(date -u -r $(($(date +%s) + 12 * 3600)) +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+12 hours' +%Y-%m-%dT%H:%M:%SZ)
run measurement-lock.sh start step5-tools "$end_utc"
lock="$MOSEMO_SESSION_DIR/measurement.lock"
if ((STATUS == 0)) && grep -qx 'node=node-loadgen' "$lock" && grep -qx 'pod=step5-tools' "$lock" &&
  [[ $(stat -f '%Lp' "$MOSEMO_SESSION_DIR/measurement.kubeconfig" 2>/dev/null || stat -c '%a' "$MOSEMO_SESSION_DIR/measurement.kubeconfig") == 600 ]] &&
  ! logged 'get pod'; then
  ok "f start without the Pod records the loadgen node and writes a 0600 kubeconfig"
else
  not_ok "f start without the Pod records the loadgen node (exit $STATUS)" "$OUTPUT"
fi
run measurement-lock.sh start step5-tools "$end_utc"
if ((STATUS != 0)) && grep -q "measurement lock" <<<"$OUTPUT"; then ok "f second start fails"; else not_ok "f second start fails (exit $STATUS)" "$OUTPUT"; fi
run measurement-lock.sh end --invalid "rehearsal"
if ((STATUS == 0)) && [[ ! -e $lock ]] && grep -qx rehearsal "$MOSEMO_SESSION_DIR/measurement/invalid" &&
  grep -q '^start=' "$MOSEMO_SESSION_DIR/measurement/window" && grep -q '^end=' "$MOSEMO_SESSION_DIR/measurement/window"; then
  ok "f end --invalid removes the lock and writes window and invalid"
else
  not_ok "f end --invalid removes the lock and writes window and invalid (exit $STATUS)" "$OUTPUT"
fi
run measurement-lock.sh start step5-tools "$end_utc"
run measurement-lock.sh end
if ((STATUS == 0)) && [[ ! -e $lock && ! -e $MOSEMO_SESSION_DIR/measurement/invalid ]]; then
  ok "f a normal end after an invalid one removes the earlier invalid"
else
  not_ok "f a normal end after an invalid one removes the earlier invalid (exit $STATUS)" "$OUTPUT"
fi
# The token lasts until 30 minutes after the expected end, clamped to 1-13 hours.
token_seconds() { sed -n 's/.*create token step5-operator --duration=\([0-9]*\)s.*/\1/p' "$FAKE_LOG" | tail -n1; }
utc_in() { date -u -r $(($(date +%s) + $1)) +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "+$1 seconds" +%Y-%m-%dT%H:%M:%SZ; }
for case_ in "36000 37740 37800" "600 3600 3600" "72000 46800 46800"; do
  read -r ahead low high <<<"$case_"
  new_case
  kubeconfig
  profile measurement
  run measurement-lock.sh start step5-tools "$(utc_in "$ahead")"
  got=$(token_seconds)
  if [[ -n $got ]] && ((got >= low && got <= high)); then
    ok "f expected end in ${ahead}s requests a ${got}s token (${low}-${high}s)"
  else
    not_ok "f expected end in ${ahead}s requests a ${low}-${high}s token, got '${got}' (exit $STATUS)" "$OUTPUT"
  fi
done
new_case
kubeconfig
profile measurement
: >"$FAKE_STATE/loadgen_nodes"
run measurement-lock.sh start step5-tools "$end_utc"
if ((STATUS != 0)) && grep -q "exactly one loadgen node" <<<"$OUTPUT" && [[ ! -e $MOSEMO_SESSION_DIR/measurement.lock ]]; then
  ok "f no loadgen node fails"
else
  not_ok "f no loadgen node fails (exit $STATUS)" "$OUTPUT"
fi
new_case
kubeconfig
profile measurement
echo $(($(date +%s) + 3600)) >"$FAKE_STATE/token_exp"
run measurement-lock.sh start step5-tools "$end_utc"
if ((STATUS != 0)) && grep -q "before the expected end" <<<"$OUTPUT" && [[ ! -e $MOSEMO_SESSION_DIR/measurement.lock ]] &&
  [[ ! -e $MOSEMO_SESSION_DIR/measurement.kubeconfig ]]; then
  ok "f token expiring before the expected end fails and leaves no kubeconfig"
else
  not_ok "f token expiring before the expected end fails (exit $STATUS)" "$OUTPUT"
fi

echo "g. a running session keeps its profile"
new_case
profile measurement
printf 'aws_eks_cluster.main\n' >"$FAKE_STATE/tfstate"
run session-up.sh --profile default
if ((STATUS != 0)) && grep -q "already runs with profile 'measurement'" <<<"$OUTPUT" && ! logged ' apply' &&
  [[ $(cat "$MOSEMO_SESSION_DIR/profile") == measurement ]]; then
  ok "g recorded profile differs from the argument with non-empty state: session-up fails before apply"
else
  not_ok "g recorded profile differs from the argument with non-empty state (exit $STATUS)" "$OUTPUT"
fi
new_case
profile measurement
mkdir -p "$MOSEMO_SESSION_DIR/measurement"
echo old >"$MOSEMO_SESSION_DIR/measurement/window"
printf 'node-kafka kafka 110 1900000Ki\nnode-app app 110 1900000Ki\nnode-loadgen loadgen 110 1900000Ki\n' >"$FAKE_STATE/nodes"
run session-up.sh
archived=$(find "$MOSEMO_SESSION_DIR" -maxdepth 1 -name 'measurement.2*' | head -n1)
if ((STATUS == 0)) && [[ -n $archived && -f $archived/window && ! -f $MOSEMO_SESSION_DIR/measurement/window ]]; then
  ok "g a new measurement session archives the previous measurement directory"
else
  not_ok "g a new measurement session archives the previous measurement directory (exit $STATUS)" "$OUTPUT"
fi

echo "h. kafka-init script grows the topic to the target and never shrinks it"
# The Job's own shell script against a fake kafka-topics that keeps the partition count in a file.
python3 -c '
import sys
text = open(sys.argv[1]).read()
block = text.split("- |\n", 1)[1].split("          securityContext:")[0]
print("\n".join(line[14:] for line in block.splitlines()))' "$DEPLOY_DIR/../kubernetes/kafka-init-job.yaml" >"$WORK/kafka-init.sh"
mkdir -p "$WORK/kafka-bin"
cat >"$WORK/kafka-bin/kafka-topics" <<'KT'
#!/bin/sh
echo "kafka-topics $*" >>"$KT_LOG"
case " $* " in
  *" --create "*) [ -f "$KT_STATE" ] || echo "$*" | sed 's/.*--partitions \([0-9]*\).*/\1/' >"$KT_STATE" ;;
  *" --alter "*) echo "$*" | sed 's/.*--partitions \([0-9]*\).*/\1/' >"$KT_STATE" ;;
  *" --describe "*) printf 'Topic: t\tTopicId: x\tPartitionCount: %s\tReplicationFactor: 1\n' "$(cat "$KT_STATE")" ;;
esac
KT
chmod +x "$WORK/kafka-bin/kafka-topics"
kafka_init() { # kafka_init TARGET EXISTING ; sets STATUS, KT_STATE, KT_LOG
  export KT_STATE="$WORK/kt-state-$1-$2" KT_LOG="$WORK/kt-log-$1-$2"
  rm -f "$KT_STATE" "$KT_LOG"
  [[ $2 == none ]] || echo "$2" >"$KT_STATE"
  set +e
  PATH="$WORK/kafka-bin:$PATH" KAFKA_TOPIC_PARTITIONS=$1 BATCH_BOOTSTRAP_SERVERS=kafka:9092 BATCH_TOPIC=t \
    sh -e "$WORK/kafka-init.sh" >/dev/null 2>&1
  STATUS=$?
  set -e
}
for case_ in "4 none 4 no" "8 none 8 no" "8 4 8 yes" "8 2 8 yes" "8 8 8 no" "4 8 8 no" "8 10 10 no"; do
  read -r target existing want altered <<<"$case_"
  kafka_init "$target" "$existing"
  got_alter=no
  grep -q -- '--alter' "$KT_LOG" && got_alter=yes
  if ((STATUS == 0)) && [[ $(cat "$KT_STATE") == "$want" && $got_alter == "$altered" ]]; then
    ok "h target $target, existing $existing -> $want partitions (alter: $altered)"
  else
    not_ok "h target $target, existing $existing -> $want partitions (alter: $altered); got $(cat "$KT_STATE" 2>/dev/null), alter $got_alter, exit $STATUS"
  fi
done
kafka_init abc none
if ((STATUS != 0)) && [[ ! -s $KT_LOG ]]; then ok "h a non-numeric target fails before any kafka-topics call"; else not_ok "h a non-numeric target fails before any kafka-topics call"; fi

echo "i. measurement-ready manifest"
# Canned AWS answers for one node group (mosemo-lab-app over subnets in 2a and 2b) on INSTANCE_TYPE nodes
# and an RDS of RDS_CLASS; ADDONS is the list-addons text ("" for none, "fail" for a denied call).
ready_case() { # ready_case INSTANCE_TYPE RDS_CLASS ADDONS
  new_case
  kubeconfig
  profile measurement
  mkdir -p "$MOSEMO_SESSION_DIR/measurement"
  echo '{"profile": "measurement", "plan_exit_code": 0}' >"$MOSEMO_SESSION_DIR/measurement/terraform.json"
  python3 - "$FAKE_STATE" "$1" "$2" <<'PY_'
import json, os, sys
state, itype, rds_class = sys.argv[1:]
w = lambda name, data: open(os.path.join(state, name), "w").write(data if isinstance(data, str) else json.dumps(data))
pools = {"app": "mosemo-lab-app", "kafka": "mosemo-lab-kafka", "loadgen": "mosemo-lab-loadgen"}
w("nodes.json", {"items": [{
    "metadata": {"name": f"node-{pool}", "labels": {"pool": pool, "eks.amazonaws.com/nodegroup": group,
                                                    "topology.kubernetes.io/zone": "ap-southeast-2a"}},
    "spec": {"providerID": f"aws:///ap-southeast-2a/i-{pool}"},
    "status": {"allocatable": {"cpu": "1930m", "memory": "7000Mi"}}} for pool, group in pools.items()]})
w("aws.ec2.describe-instances", {"Reservations": [{"Instances": [
    {"InstanceId": f"i-{pool}", "InstanceType": itype, "LaunchTime": "2026-11-20T09:00:00+00:00",
     "Placement": {"AvailabilityZone": "ap-southeast-2a"}} for pool in pools]}]})
w("aws.ec2.describe-instance-credit-specifications",
  {"InstanceCreditSpecifications": [{"InstanceId": f"i-{pool}", "CpuCredits": "standard"} for pool in pools]})
w("aws.ec2.describe-subnets", {"Subnets": [{"SubnetId": "subnet-a", "AvailabilityZone": "ap-southeast-2a"},
                                          {"SubnetId": "subnet-b", "AvailabilityZone": "ap-southeast-2b"}]})
w("aws.eks.describe-cluster", {"cluster": {"version": "1.36"}})
w("aws.eks.describe-addon", {"addon": {"addonName": "vpc-cni", "addonVersion": "v1.20.0-eksbuild.1"}})
w("aws.eks.list-nodegroups", "mosemo-lab-app\n")
w("aws.eks.describe-nodegroup", {"nodegroup": {
    "nodegroupName": "mosemo-lab-app", "instanceTypes": None, "amiType": "AL2023_x86_64_STANDARD",
    "releaseVersion": "1.36.5-20261107", "subnets": ["subnet-b", "subnet-a"],
    "scalingConfig": {"desiredSize": 1}, "taints": []}})
w("aws.rds.describe-db-instances", {"DBInstances": [{
    "EngineVersion": "18.3", "DBInstanceClass": rds_class, "StorageType": "gp3", "AllocatedStorage": 20,
    "DBParameterGroups": [{"DBParameterGroupName": "default.postgres18"}], "AvailabilityZone": "ap-southeast-2a"}]})
w("aws.cloudwatch.get-metric-data", {"MetricDataResults": []})
PY_
  case $3 in
    fail)
      echo "AccessDeniedException: not authorized to perform eks:ListAddons" >"$FAKE_STATE/aws.eks.list-addons"
      echo 254 >"$FAKE_STATE/aws.eks.list-addons.exit"
      ;;
    *) printf '%s\n' "$3" >"$FAKE_STATE/aws.eks.list-addons" ;;
  esac
  run measurement-ready.sh a3b4c5d6e7f8
  MANIFEST="$MOSEMO_SESSION_DIR/measurement/manifest.json"
  REPORT="$MOSEMO_SESSION_DIR/measurement/ready.txt"
  CREDIT_QUERIES="$MOSEMO_SESSION_DIR/measurement/ready-raw/credit-queries.json"
}
manifest_is() { # manifest_is PYTHON_EXPRESSION_ON_m
  python3 -c "import json,sys; m=json.load(open(sys.argv[1])); sys.exit(0 if ($1) else 1)" "$MANIFEST" 2>/dev/null
}
ready_case t3.small db.t4g.micro vpc-cni
if grep -qx 'PASS P9 EKS add-ons: vpc-cni' "$REPORT" && manifest_is 'm["eks"]["addons"] == {"vpc-cni": "v1.20.0-eksbuild.1"}'; then
  ok "i listed add-ons pass P9 and reach eks.addons"
else
  not_ok "i listed add-ons pass P9 and reach eks.addons (exit $STATUS)" "$OUTPUT"
fi
if manifest_is 'm["nodegroups"][0]["credit"] == "standard"' && logged '^aws\|.*ec2 describe-instance-credit-specifications --instance-ids i-app i-kafka i-loadgen '; then
  ok "i t3 node group credit is the instances' CpuCredits (standard)"
else
  not_ok "i t3 node group credit is the instances' CpuCredits (exit $STATUS)" "$OUTPUT"
fi
if manifest_is 'm["nodegroups"][0]["availability_zones"] == ["ap-southeast-2a", "ap-southeast-2b"]'; then
  ok "i node group availability_zones come from its subnets"
else
  not_ok "i node group availability_zones come from its subnets (exit $STATUS)" "$OUTPUT"
fi
if manifest_is 'all(n["instance_type"] == "t3.small" for n in m["nodes"]) and len(m["nodes"]) == 3'; then
  ok "i nodes[] carry instance_type"
else
  not_ok "i nodes[] carry instance_type (exit $STATUS)" "$OUTPUT"
fi
if manifest_is 'm["rds"]["credit"] == "unlimited"' && grep -q '"rds mosemo-lab"' "$CREDIT_QUERIES"; then
  ok "i db.t* RDS: credit unlimited and P8 queries its CPUCreditBalance"
else
  not_ok "i db.t* RDS: credit unlimited and P8 queries its CPUCreditBalance (exit $STATUS)" "$OUTPUT"
fi
ready_case m7i.large db.m7g.large vpc-cni
if manifest_is 'm["nodegroups"][0]["instance_type"] == "m7i.large"'; then
  ok "i a launch-template node group (instanceTypes null) records its instances' type"
else
  not_ok "i a launch-template node group (instanceTypes null) records its instances' type (exit $STATUS)" "$OUTPUT"
fi
if manifest_is 'm["nodegroups"][0]["credit"] == "none"' && ! logged 'describe-instance-credit-specifications'; then
  ok "i m7i node group credit is none without a credit lookup"
else
  not_ok "i m7i node group credit is none without a credit lookup (exit $STATUS)" "$OUTPUT"
fi
if manifest_is 'm["rds"]["credit"] == "none"' && ! grep -q '"rds mosemo-lab"' "$CREDIT_QUERIES" && ! grep -q 'P8 CPUCreditBalance rds' "$REPORT"; then
  ok "i non-T RDS: credit none and no P8 RDS CPUCreditBalance"
else
  not_ok "i non-T RDS: credit none and no P8 RDS CPUCreditBalance (exit $STATUS)" "$OUTPUT"
fi
for addons in "" fail; do
  ready_case m7i.large db.m7g.large "$addons"
  if ((STATUS != 0)) && grep -qx 'FAIL P9 eks list-addons failed or listed no add-ons' "$REPORT" && grep -q '^RESULT FAIL' "$REPORT"; then
    ok "i list-addons ${addons:-empty} fails P9 and the result"
  else
    not_ok "i list-addons ${addons:-empty} fails P9 and the result (exit $STATUS)" "$OUTPUT"
  fi
done
printf '\n%s passed, %s failed\n' "$passed" "$failed"
((failed == 0))

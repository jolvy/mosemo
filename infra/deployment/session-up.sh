#!/usr/bin/env bash
# Start a work session: session stack apply (+ no-drift plan) -> session kubeconfig -> nodes, allocatable
# memory -> namespace, ConfigMap, Secret, RDS CA -> Kafka NetworkPolicy, StorageClass, StatefulSet and
# topic Job. Stops at the first failed check.
# Usage: session-up.sh [--profile default|measurement]. Without --profile the recorded profile is reused.
# Requires: ../terraform/foundation applied, AWS login, CLOUDFLARE_API_TOKEN, $MOSEMO_SESSION_DIR/secrets.env.
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

requested=""
while (($# > 0)); do
  case $1 in
    --profile)
      [[ $# -ge 2 ]] || fail "--profile needs a value: default or measurement"
      requested=$2
      shift 2
      ;;
    *) fail "unknown argument: $1 (usage: session-up.sh [--profile default|measurement])" ;;
  esac
done
case $requested in
  "" | default | measurement) ;;
  *) fail "unknown profile: $requested (default or measurement)" ;;
esac

step "Preflight"
require_tools terraform aws kubectl curl python3 git
check_session_dir
check_secrets_file
check_cloudflare_token
check_aws_login
load_db_password
tf_init

step "Session profile"
recorded=""
[[ -f $PROFILE_FILE ]] && recorded=$(cat "$PROFILE_FILE")
state=$(tf state list)
if [[ -n $state ]]; then
  # A running session keeps its profile; switching would add or remove a node group mid-session.
  [[ -n $recorded ]] || recorded=default
  [[ -z $requested || $requested == "$recorded" ]] \
    || fail "the session already runs with profile '$recorded'; finish it with session-down.sh before starting '$requested'"
  profile=$recorded
else
  profile=${requested:-${recorded:-default}}
  if [[ $profile == measurement && -d $MEASUREMENT_DIR ]]; then
    archived="$MEASUREMENT_DIR.$(date -u +%Y%m%dT%H%M%SZ)"
    mv "$MEASUREMENT_DIR" "$archived"
    echo "previous measurement results moved to $archived"
  fi
fi
printf '%s\n' "$profile" >"$PROFILE_FILE"
echo "profile: $profile"

step "Apply session stack (review the plan, including the Cloudflare CNAME, before answering yes)"
tf apply -input=true

step "No drift after apply"
# Same environment as the apply (see tf); any pending change would alter the measured lab later.
drift_plan="$SESSION_DIR/drift.txt"
if tf plan -input=false -no-color -detailed-exitcode >"$drift_plan" 2>&1; then
  plan_exit_code=0
else
  plan_exit_code=$?
fi
if [[ $profile == measurement ]]; then
  mkdir -p "$MEASUREMENT_DIR"
  commit=$(git -C "$REPO_ROOT" rev-parse HEAD)
  git -C "$REPO_ROOT" status --porcelain | python3 -c '
import datetime, json, sys
lines = [line.rstrip("\n") for line in sys.stdin if line.strip()]
json.dump({
    "commit": sys.argv[1],
    "dirty_files": [line[3:] for line in lines],
    "plan_exit_code": int(sys.argv[2]),
    "profile": sys.argv[3],
    "captured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, open(sys.argv[4], "w"), indent=2)' "$commit" "$plan_exit_code" "$profile" "$MEASUREMENT_DIR/terraform.json"
fi
if ((plan_exit_code != 0)); then
  grep -E '^  # |^Plan:|^Error' "$drift_plan" >&2 || true
  fail "terraform plan after apply exited $plan_exit_code (full output: $drift_plan)"
fi
rm -f "$drift_plan"
echo "plan after apply: no changes"

step "Session kubeconfig"
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION" --profile "$AWS_PROFILE_NAME" \
  --kubeconfig "$KUBECONFIG" >/dev/null
chmod 600 "$KUBECONFIG"
[[ $(kubectl config current-context) == *":cluster/$CLUSTER_NAME" ]] || fail "kubeconfig does not target $CLUSTER_NAME"

step "Nodes, prefix delegation and allocatable memory"
pools="kafka app"
[[ $profile == measurement ]] && pools="$pools loadgen"
pool_count=0
for pool in $pools; do
  kubectl wait --for=condition=Ready node -l "pool=$pool" --timeout=10m >/dev/null \
    || fail "no Ready node in pool $pool"
  ((pool_count += 1))
done
[[ $(kubectl -n kube-system get daemonset aws-node \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="aws-node")].env[?(@.name=="ENABLE_PREFIX_DELEGATION")].value}') == true ]] \
  || fail "aws-node does not have ENABLE_PREFIX_DELEGATION=true"
# t3.small allows 11 Pods without prefixes; managed node groups cap prefix mode at 110.
# kube-reserved grows with max-pods (11 x maxPods + 255Mi); the app node must still fit the
# deploy-time requests measured for G0 (1236Mi), otherwise stop and decide.
node_info=$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.labels.pool}{" "}{.status.allocatable.pods}{" "}{.status.allocatable.memory}{"\n"}{end}')
checked=0
while read -r node pool pods memory; do
  memory_mib=$(quantity_mib "$memory")
  echo "$node pool=$pool allocatable pods=$pods memory=${memory_mib}Mi"
  ((pods > 11)) || fail "$node still has the secondary-IP Pod limit; prefix delegation was not applied at launch"
  if [[ $pool == app ]] && ((memory_mib < 1236)); then
    fail "allocatable memory too low on app node $node: ${memory_mib}Mi < 1236Mi"
  fi
  ((checked += 1))
done <<<"$node_info"
((checked >= pool_count)) || fail "expected a node in each pool ($pools), checked $checked"
kubectl -n kube-system rollout status daemonset/ebs-csi-node --timeout=5m >/dev/null
[[ $(kubectl -n kube-system get daemonset ebs-csi-node -o jsonpath='{.status.numberReady}') == \
  $(kubectl get nodes --no-headers | wc -l | tr -d ' ') ]] || fail "EBS CSI node plugin is not ready on every node"

step "Namespace, priorities and container defaults"
kubectl apply -f "$K8S_DIR/namespace.yaml" -f "$K8S_DIR/priority-classes.yaml" -f "$K8S_DIR/limit-range.yaml" >/dev/null

step "Application ConfigMap"
db_host=$(tf output -json database | python3 -c 'import json,sys; print(json.load(sys.stdin)["DB_HOST"])')
[[ $db_host =~ ^$CLUSTER_NAME\.[a-z0-9]+\.$AWS_REGION\.rds\.amazonaws\.com$ ]] || fail "unexpected RDS endpoint: $db_host"
sed "s|REPLACE_WITH_RDS_ENDPOINT|$db_host|" "$K8S_DIR/configmap.yaml" | kubectl apply -f - >/dev/null

step "Application Secret (server-side apply; values are never printed)"
kubectl -n "$NAMESPACE" create secret generic mosemo-secrets --from-env-file="$SECRETS_FILE" --dry-run=client -o yaml \
  | kubectl apply --server-side --force-conflicts -f - >/dev/null

step "RDS CA bundle"
curl -fsS "https://truststore.pki.rds.amazonaws.com/$AWS_REGION/$AWS_REGION-bundle.pem" -o "$SESSION_DIR/rds-ca.pem"
grep -q 'BEGIN CERTIFICATE' "$SESSION_DIR/rds-ca.pem" || fail "downloaded RDS CA bundle is not a PEM file"
kubectl -n "$NAMESPACE" create configmap mosemo-rds-ca --from-file=bundle.pem="$SESSION_DIR/rds-ca.pem" \
  --dry-run=client -o yaml | kubectl apply --server-side --force-conflicts -f - >/dev/null

step "Kafka"
# The ingress policy exists before the broker so it never runs unprotected.
kubectl apply -f "$K8S_DIR/network-policies.yaml" >/dev/null
kubectl apply -f "$K8S_DIR/kafka-storage-class.yaml" -f "$K8S_DIR/kafka.yaml" >/dev/null
kubectl -n "$NAMESPACE" rollout status statefulset/mosemo-kafka --timeout=10m
[[ $(kubectl -n "$NAMESPACE" get pvc data-mosemo-kafka-0 -o jsonpath='{.status.phase}') == Bound ]] \
  || fail "Kafka PVC is not Bound"
kafka_node=$(kubectl -n "$NAMESPACE" get pod mosemo-kafka-0 -o jsonpath='{.spec.nodeName}')
[[ $(kubectl get node "$kafka_node" -o jsonpath='{.metadata.labels.pool}') == kafka ]] \
  || fail "Kafka is not running on the kafka node group"
run_job "$K8S_DIR/kafka-init-job.yaml" mosemo-kafka-init 210

step "Session is up (profile $profile)"
echo "Next: bash infra/deployment/deploy.sh"

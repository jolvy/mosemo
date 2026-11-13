#!/usr/bin/env bash
# Start a work session: session stack apply -> session kubeconfig -> namespace, ConfigMap,
# Secret, RDS CA -> Kafka NetworkPolicy, StorageClass, StatefulSet and topic Job. Stops at the first failed check.
# Requires: ../terraform/foundation applied, AWS login, CLOUDFLARE_API_TOKEN, $MOSEMO_SESSION_DIR/secrets.env.
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

step "Preflight"
require_tools terraform aws kubectl curl python3
check_session_dir
check_secrets_file
check_cloudflare_token
check_aws_login
load_db_password

step "Apply session stack (review the plan, including the Cloudflare CNAME, before answering yes)"
tf_init
tf apply -input=true

step "Session kubeconfig"
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION" --profile "$AWS_PROFILE_NAME" \
  --kubeconfig "$KUBECONFIG" >/dev/null
chmod 600 "$KUBECONFIG"
[[ $(kubectl config current-context) == *":cluster/$CLUSTER_NAME" ]] || fail "kubeconfig does not target $CLUSTER_NAME"

step "Nodes and prefix delegation"
for pool in kafka app; do
  kubectl wait --for=condition=Ready node -l "pool=$pool" --timeout=10m >/dev/null \
    || fail "no Ready node in pool $pool"
done
[[ $(kubectl -n kube-system get daemonset aws-node \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="aws-node")].env[?(@.name=="ENABLE_PREFIX_DELEGATION")].value}') == true ]] \
  || fail "aws-node does not have ENABLE_PREFIX_DELEGATION=true"
# t3.small allows 11 Pods without prefixes; managed node groups cap prefix mode at 110.
node_pods=$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.labels.pool}{" "}{.status.allocatable.pods}{"\n"}{end}')
checked=0
while read -r node pool pods; do
  echo "$node pool=$pool allocatable pods=$pods"
  ((pods > 11)) || fail "$node still has the secondary-IP Pod limit; prefix delegation was not applied at launch"
  ((checked += 1))
done <<<"$node_pods"
((checked >= 2)) || fail "expected at least the kafka and app nodes, checked $checked"
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

step "Session is up"
echo "Next: bash infra/deployment/deploy.sh"

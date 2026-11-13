#!/usr/bin/env bash
# Server-side validation of infra/kubernetes on a throwaway kind cluster (deleted on exit):
#   1. namespace and PriorityClasses applied for real, every other manifest `kubectl apply --dry-run=server`
#      (configmap rendered with a sample RDS endpoint);
#   2. measurement-rbac.yaml applied and the step5-operator `kubectl auth can-i` answers compared with
#      measurement-rbac-matrix.txt (the same matrix measurement-ready.sh P1 checks on EKS).
# Requires Docker and kind. Usage: bash infra/deployment/tests/kind-check.sh
set -euo pipefail

DEPLOY_DIR=$(cd "$(dirname "$0")/.." && pwd -P)
K8S_DIR=$(cd "$DEPLOY_DIR/../kubernetes" && pwd -P)
WORK=$(mktemp -d)
CLUSTER="mosemo-kind-check-$$"
export KUBECONFIG="$WORK/kubeconfig"
cleanup() {
  kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" --wait 120s >/dev/null
failed=0

echo "1. server dry-run"
kubectl apply -f "$K8S_DIR/namespace.yaml" -f "$K8S_DIR/priority-classes.yaml" >/dev/null
sed 's|REPLACE_WITH_RDS_ENDPOINT|mosemo-lab.abc123.ap-southeast-2.rds.amazonaws.com|' "$K8S_DIR/configmap.yaml" >"$WORK/configmap.yaml"
for manifest in "$K8S_DIR"/*.yaml "$WORK/configmap.yaml"; do
  case $(basename "$manifest") in namespace.yaml | priority-classes.yaml) continue ;; esac
  [[ $manifest == "$K8S_DIR/configmap.yaml" ]] && continue
  if out=$(kubectl apply --dry-run=server -f "$manifest" 2>&1); then
    printf 'ok   %s\n' "$(basename "$manifest")"
  else
    printf 'FAIL %s\n%s\n' "$(basename "$manifest")" "$out"
    failed=$((failed + 1))
  fi
done

echo "2. step5-operator RBAC matrix"
kubectl apply -f "$K8S_DIR/measurement-rbac.yaml" >/dev/null
while read -r expected verb resource subresource; do
  [[ -z $expected || $expected == \#* ]] && continue
  if [[ -n $subresource ]]; then
    answer=$(kubectl auth can-i "$verb" "$resource" --subresource="$subresource" -n mosemo \
      --as=system:serviceaccount:mosemo:step5-operator 2>/dev/null || true)
  else
    answer=$(kubectl auth can-i "$verb" "$resource" -n mosemo --as=system:serviceaccount:mosemo:step5-operator 2>/dev/null || true)
  fi
  label="can-i $verb $resource${subresource:+ ($subresource)}"
  if [[ $answer == "$expected" ]]; then
    printf 'ok   %s = %s\n' "$label" "$answer"
  else
    printf 'FAIL %s = %s (expected %s)\n' "$label" "$answer" "$expected"
    failed=$((failed + 1))
  fi
done <"$DEPLOY_DIR/measurement-rbac-matrix.txt"

printf '\n%s failed\n' "$failed"
((failed == 0))

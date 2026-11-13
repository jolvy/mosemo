# Shared by session-up.sh, deploy.sh and session-down.sh; source, do not run.
# Never print secret values: no `set -x`, and secrets only flow through files and pipes.
# shellcheck disable=SC2034 # Constants are used by the sourcing scripts.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
TF_DIR="$REPO_ROOT/infra/terraform/infrastructure"
K8S_DIR="$REPO_ROOT/infra/kubernetes"
AWS_PROFILE_NAME=mosemo-infra
AWS_REGION=ap-southeast-2
CLUSTER_NAME=mosemo-lab
FINAL_SNAPSHOT_ID=$CLUSTER_NAME-final # rds.tf final_snapshot_identifier
NAMESPACE=mosemo
API_HOST=mosemo-api.danuh.com

# Outside the repository, mode 0700. Holds secrets.env (0600), the session kubeconfig and rendered manifests.
SESSION_DIR=${MOSEMO_SESSION_DIR:-$HOME/.mosemo/lab}
SECRETS_FILE="$SESSION_DIR/secrets.env"
RENDER_DIR="$SESSION_DIR/rendered"
export KUBECONFIG="$SESSION_DIR/kubeconfig"
SECRET_KEYS=(DB_PASSWORD AUTH_JWT_SECRET_KEY KAKAO_REST_API_KEY KAKAO_CLIENT_SECRET GOOGLE_API_KEY)

step() { printf '\n==> %s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

require_tools() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null || fail "required tool not found: $tool"
  done
}

file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }

check_session_dir() {
  [[ -d $SESSION_DIR ]] || fail "create $SESSION_DIR (mode 0700) outside the repository"
  case "$(cd "$SESSION_DIR" && pwd -P)/" in
    "$REPO_ROOT"/*) fail "MOSEMO_SESSION_DIR must be outside the repository" ;;
  esac
  [[ $(file_mode "$SESSION_DIR") == 700 ]] || fail "$SESSION_DIR must have mode 0700"
}

# KEY=value lines (no quotes), exactly the keys in SECRET_KEYS.
check_secrets_file() {
  local key allowed
  [[ -f $SECRETS_FILE ]] || fail "missing $SECRETS_FILE"
  [[ $(file_mode "$SECRETS_FILE") == 600 ]] || fail "$SECRETS_FILE must have mode 0600"
  for key in "${SECRET_KEYS[@]}"; do
    grep -q "^$key=." "$SECRETS_FILE" || fail "$SECRETS_FILE has no value for $key"
  done
  allowed=$(IFS='|'; printf '%s' "${SECRET_KEYS[*]}")
  if grep -vE '^(#|$)' "$SECRETS_FILE" | grep -vE "^($allowed)=" >/dev/null; then
    fail "$SECRETS_FILE may only contain: ${SECRET_KEYS[*]}"
  fi
}

# RDS and the application Secret share one password; Terraform takes it as an ephemeral variable.
# Kept unexported so only terraform (see tf) receives it, not kubectl, aws, curl or docker.
load_db_password() {
  db_password=$(sed -n 's/^DB_PASSWORD=//p' "$SECRETS_FILE")
}

check_cloudflare_token() {
  [[ -n ${CLOUDFLARE_API_TOKEN:-} ]] || fail "CLOUDFLARE_API_TOKEN is not set (danuh.com Zone:DNS:Edit token)"
}

check_aws_login() {
  aws sts get-caller-identity --profile "$AWS_PROFILE_NAME" --query Arn --output text >/dev/null \
    || fail "AWS profile $AWS_PROFILE_NAME has no valid session; sign in again and retry"
}

check_cluster_access() {
  [[ -f $KUBECONFIG ]] || fail "missing $KUBECONFIG; run session-up.sh first"
  kubectl get namespace "$NAMESPACE" >/dev/null || fail "cannot reach namespace $NAMESPACE in the session cluster"
}

tf() {
  if [[ -n ${db_password:-} ]]; then
    TF_VAR_db_password=$db_password terraform -chdir="$TF_DIR" "$@"
  else
    terraform -chdir="$TF_DIR" "$@"
  fi
}

tf_init() { tf init -input=false -backend-config=backend.hcl >/dev/null; }

# Recreates a finished Job and waits for its outcome; refuses to touch a running one.
run_job() {
  local manifest=$1 name=$2 timeout=$3 active elapsed=0
  if kubectl -n "$NAMESPACE" get job "$name" >/dev/null 2>&1; then
    active=$(kubectl -n "$NAMESPACE" get job "$name" -o jsonpath='{.status.active}')
    [[ -z $active || $active == 0 ]] || fail "job/$name is still running; not replacing it"
    kubectl -n "$NAMESPACE" delete job "$name" --wait=true >/dev/null
  fi
  kubectl apply -f "$manifest" >/dev/null
  while ((elapsed < timeout)); do
    if [[ $(kubectl -n "$NAMESPACE" get job "$name" -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}') == True ]]; then
      echo "job/$name complete"
      return
    fi
    if [[ $(kubectl -n "$NAMESPACE" get job "$name" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}') == True ]]; then
      fail "job/$name failed; inspect with: kubectl -n $NAMESPACE logs job/$name"
    fi
    sleep 5
    ((elapsed += 5))
  done
  fail "job/$name did not finish within ${timeout}s"
}

# Reads `terraform show -json` of the skip_final_snapshot plan on stdin and prints what is not allowed.
# Allowed: the database update, and on a rerun the ALB update that only restores enable_deletion_protection.
snapshot_plan_violations() {
  python3 -c '
import json, sys
plan = json.load(sys.stdin)
for c in plan.get("resource_changes", []):
    actions = c["change"]["actions"]
    if actions == ["no-op"]:
        continue
    if c["address"] == "aws_db_instance.main" and actions == ["update"]:
        continue
    if c["address"] == "aws_lb.api" and actions == ["update"]:
        before = c["change"].get("before") or {}
        after = c["change"].get("after") or {}
        unknown = c["change"].get("after_unknown") or {}
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        changed |= {k for k, v in unknown.items() if v}
        if changed == {"enable_deletion_protection"}:
            continue
        print("aws_lb.api:update(" + ",".join(sorted(changed)) + ")")
        continue
    print(c["address"] + ":" + "+".join(actions))'
}

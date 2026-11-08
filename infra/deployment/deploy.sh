#!/usr/bin/env bash
# Deploy the current commit: image digest (build and push only when the commit has no image) ->
# migration Job -> API and consumer rollout -> smoke check. Stops at the first failed check.
# Run after session-up.sh; kept free of interactive steps so G2 CI can reuse it.
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

step "Preflight"
require_tools terraform aws kubectl docker git curl dig
check_session_dir
check_aws_login
check_cluster_access
[[ -z $(git -C "$REPO_ROOT" status --porcelain) ]] || fail "commit or discard local changes; images are tagged by commit"
commit=$(git -C "$REPO_ROOT" rev-parse HEAD)
tf_init
repo_url=$(tf output -raw ecr_repository_url)
# The registry host receives an ECR token below; accept only this account's Sydney repository.
[[ $repo_url =~ ^[0-9]{12}\.dkr\.ecr\.$AWS_REGION\.amazonaws\.com/mosemo-lab-server$ ]] || fail "unexpected ECR repository URL: $repo_url"
alb_dns=$(tf output -raw alb_dns_name)
mkdir -p "$RENDER_DIR"

image_digest() {
  aws ecr describe-images --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION" \
    --repository-name "${repo_url#*/}" --image-ids "imageTag=$commit" \
    --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true
}

step "Image for $commit"
digest=$(image_digest)
if [[ $digest != sha256:* ]]; then
  docker buildx build --platform linux/amd64 --load -t "$repo_url:$commit" "$REPO_ROOT"
  aws ecr get-login-password --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin "${repo_url%%/*}" >/dev/null
  docker push "$repo_url:$commit" >/dev/null
  docker logout "${repo_url%%/*}" >/dev/null
  digest=$(image_digest)
  [[ $digest == sha256:* ]] || fail "pushed image has no digest in ECR"
else
  echo "reusing the existing image for this commit"
fi
image="$repo_url@$digest"
echo "image: $image"

render() { kubectl set image --local -f "$K8S_DIR/$1" "$2=$image" -o yaml >"$RENDER_DIR/$1"; }
render migration-job.yaml migration
render deployment.yaml server
render consumer-deployment.yaml consumer

step "Migration"
run_job "$RENDER_DIR/migration-job.yaml" mosemo-migration 330

step "API and consumer rollout"
kubectl apply -f "$RENDER_DIR/deployment.yaml" -f "$RENDER_DIR/consumer-deployment.yaml" -f "$K8S_DIR/service.yaml" >/dev/null
for deployment in mosemo-server mosemo-consumer; do
  kubectl -n "$NAMESPACE" rollout status "deployment/$deployment" --timeout=5m
  [[ $(kubectl -n "$NAMESPACE" get deployment "$deployment" -o jsonpath='{.spec.template.spec.containers[0].image}') == "$image" ]] \
    || fail "deployment/$deployment does not run $image"
done

step "Smoke check"
# Pin the connection to this session's ALB so stale resolver caches cannot hide a failure.
via_alb=(--connect-to "$API_HOST:443:$alb_dns:443" --connect-to "$API_HOST:80:$alb_dns:80")
status() { curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${via_alb[@]}" "$1" || true; }
for ((i = 0; i < 60; i++)); do
  [[ $(status "https://$API_HOST/openapi.json") == 200 ]] && break
  sleep 5
done
[[ $(status "https://$API_HOST/openapi.json") == 200 ]] || fail "HTTPS /openapi.json did not return 200 through the ALB"
[[ $(status "https://$API_HOST/api/v1/accounts/me") == 401 ]] || fail "unauthenticated /api/v1/accounts/me did not return 401"
[[ $(status "http://$API_HOST/openapi.json") == 301 ]] || fail "HTTP did not redirect to HTTPS"
# Resolver caches from a previous session expire within the record TTL (300s) or the zone's negative TTL.
cname_ok=false
for ((i = 0; i < 40; i++)); do
  if [[ $(dig +short @1.1.1.1 "$API_HOST" CNAME) == "$alb_dns." ]]; then
    cname_ok=true
    break
  fi
  sleep 15
done
[[ $cname_ok == true ]] || fail "$API_HOST CNAME does not point to $alb_dns after 10 minutes"
group_stable=false
for ((i = 0; i < 30; i++)); do
  if kubectl -n "$NAMESPACE" exec mosemo-kafka-0 -- env KAFKA_HEAP_OPTS='-Xms32m -Xmx64m' kafka-consumer-groups \
    --bootstrap-server localhost:9092 --describe --group mosemo.activity-batches --state 2>/dev/null | grep Stable >/dev/null; then
    group_stable=true
    break
  fi
  sleep 2
done
[[ $group_stable == true ]] || fail "consumer group mosemo.activity-batches is not Stable"

step "Deployed $image"
echo "Not checked: authenticated activity flow to DB, real Kakao login."

#!/usr/bin/env bash
# Deploy the current commit: image digest (build and push only when the commit has no image) ->
# migration Job -> API, consumer and batch rollout -> smoke check. Stops at the first failed check.
# The measurement profile (see session-up.sh) never runs batch: it is skipped and an existing one removed.
# It runs API and consumer with 4 replicas spread one per app node, and checks that placement after the rollout.
# Run after session-up.sh; kept free of interactive steps so G2 CI can reuse it.
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

check_measurement_lock
profile=$(session_profile)
workloads="mosemo-server mosemo-consumer mosemo-batch"
[[ $profile == measurement ]] && workloads="mosemo-server mosemo-consumer"

step "Preflight (profile $profile)"
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
# Measurement profile: the app group is 4 m7i.large nodes and each runs one API and one consumer Pod.
# Topic partitions 8 = consumer 4 Pods x max_workers 2. Reads kubectl -o yaml output (template spec at 4 spaces).
measurement_render() { # FILE APP
  awk -v app="$2" '
    /^  replicas: / { print "  replicas: 4"; next }
    { print }
    /^    spec:$/ {
      print "      topologySpreadConstraints:"
      print "      - maxSkew: 1"
      print "        topologyKey: kubernetes.io/hostname"
      print "        whenUnsatisfiable: DoNotSchedule"
      print "        nodeTaintsPolicy: Honor"
      print "        labelSelector:"
      print "          matchLabels:"
      print "            app: " app
    }' "$1" >"$1.tmp"
  mv "$1.tmp" "$1"
  [[ $(grep -cx '  replicas: 4' "$1") -eq 1 && $(grep -cx '      topologySpreadConstraints:' "$1") -eq 1 ]] \
    || fail "could not render replicas 4 and the hostname spread into $1"
}
render migration-job.yaml migration
render deployment.yaml server
render consumer-deployment.yaml consumer
if [[ $profile == measurement ]]; then
  measurement_render "$RENDER_DIR/deployment.yaml" mosemo-server
  measurement_render "$RENDER_DIR/consumer-deployment.yaml" mosemo-consumer
else
  render batch-deployment.yaml batch
fi

step "Migration"
run_job "$RENDER_DIR/migration-job.yaml" mosemo-migration 330

step "Rollout: $workloads"
kubectl apply -f "$RENDER_DIR/deployment.yaml" -f "$RENDER_DIR/consumer-deployment.yaml" -f "$K8S_DIR/service.yaml" >/dev/null
if [[ $profile == measurement ]]; then
  kubectl -n "$NAMESPACE" delete deployment mosemo-batch --ignore-not-found --wait=true >/dev/null
  echo "batch is not deployed in the measurement profile"
else
  kubectl apply -f "$RENDER_DIR/batch-deployment.yaml" >/dev/null
fi
for deployment in $workloads; do
  kubectl -n "$NAMESPACE" rollout status "deployment/$deployment" --timeout=5m
  [[ $(kubectl -n "$NAMESPACE" get deployment "$deployment" -o jsonpath='{.spec.template.spec.containers[0].image}') == "$image" ]] \
    || fail "deployment/$deployment does not run $image"
done
if [[ $profile == measurement ]]; then
  # Sorted node lists are equal only when every app node runs exactly one Pod and no Pod runs elsewhere.
  app_nodes=$(kubectl get nodes -l pool=app -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort)
  for deployment in mosemo-server mosemo-consumer; do
    pod_nodes=$(kubectl -n "$NAMESPACE" get pods -l "app=$deployment" \
      -o jsonpath='{range .items[*]}{.metadata.deletionTimestamp}{" "}{.spec.nodeName}{"\n"}{end}' | awk 'NF == 1 { print $1 }' | sort)
    [[ -n $app_nodes && $pod_nodes == "$app_nodes" ]] \
      || fail "$deployment Pods are not one per app node: nodes [$(tr '\n' ' ' <<<"$app_nodes")], Pods on [$(tr '\n' ' ' <<<"$pod_nodes")]"
  done
  echo "one mosemo-server and one mosemo-consumer Pod on each app node: $(tr '\n' ' ' <<<"$app_nodes")"
fi

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
if [[ $profile != measurement ]]; then
  # A Running pod is not evidence: the label proposal sweep runs on start and must log a success.
  proposals_ok=false
  for ((i = 0; i < 36; i++)); do
    if kubectl -n "$NAMESPACE" logs deployment/mosemo-batch 2>/dev/null \
      | grep '"operation":"label-proposals","outcome":"success"' >/dev/null; then
      proposals_ok=true
      break
    fi
    sleep 5
  done
  [[ $proposals_ok == true ]] || fail "batch label-proposals job did not log a successful run"
fi

step "Deployed $image"
# The smoke reaches the API without authentication and never writes to the database.
echo "Not checked: authenticated API calls, the activity flow API -> Kafka -> consumer -> DB, real Kakao login."
[[ $profile != measurement ]] || echo "Not deployed (measurement profile): batch label proposals."

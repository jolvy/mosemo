#!/usr/bin/env bash
# Step 5 readiness check (measurement profile only), run after deploy.sh and before measurement-lock.sh start.
# Checks P1-P9 of the measurement side's requirements and writes $SESSION_DIR/measurement/ready.txt,
# manifest.json and manifest.sha256. Exits 1 when any check fails; the measurement must not start then.
# Usage: measurement-ready.sh EXPECTED_ALEMBIC_HEAD
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

check_measurement_lock
[[ $# -eq 1 && $1 =~ ^[0-9a-f]{12}$ ]] || fail "usage: measurement-ready.sh EXPECTED_ALEMBIC_HEAD (12 hex characters)"
expected_head=$1

step "Preflight"
require_tools aws kubectl python3
check_session_dir
[[ $(session_profile) == measurement ]] || fail "run session-up.sh --profile measurement first"
[[ -f $MEASUREMENT_DIR/terraform.json ]] || fail "missing $MEASUREMENT_DIR/terraform.json (written by session-up.sh)"
check_aws_login
check_cluster_access

awscli() { aws "$@" --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION"; }
k() { kubectl -n "$NAMESPACE" "$@"; }

raw="$MEASUREMENT_DIR/ready-raw"
rm -rf "$raw"
mkdir -p "$raw"
report="$MEASUREMENT_DIR/ready.txt"
printf 'measurement-ready %s expected_alembic_head=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$expected_head" >"$report"
failures=0
pass() { printf 'PASS %s\n' "$*" | tee -a "$report"; }
info() { printf 'INFO %s\n' "$*" | tee -a "$report"; }
failed() {
  printf 'FAIL %s\n' "$*" | tee -a "$report"
  ((failures += 1))
}
check() { # check LABEL CONDITION_EXIT_STATUS...
  local label=$1
  shift
  if "$@"; then pass "$label"; else failed "$label"; fi
}

# One-off Pod on the loadgen node with the application's configuration, like the measurement Pod.
# Prints its logs; leaves phase, node and imageID in $raw/<name>.pod.json. Deleted afterwards.
run_tool_pod() {
  local name=$1 image=$2 script=$3 phase="" elapsed=0
  k delete pod "$name" --ignore-not-found --wait=true >/dev/null
  python3 - "$name" "$image" "$script" "$NAMESPACE" <<'PY' | kubectl apply -f - >/dev/null
import json, sys
name, image, script, namespace = sys.argv[1:]
env = [{"name": "HOME", "value": "/tmp"}] + [
    {"name": pg, "valueFrom": {"configMapKeyRef": {"name": "mosemo-config", "key": key}}}
    for pg, key in [("PGHOST", "DB_HOST"), ("PGPORT", "DB_PORT"), ("PGDATABASE", "DB_DATABASE"),
                    ("PGUSER", "DB_USER"), ("PGSSLMODE", "PGSSLMODE"), ("PGSSLROOTCERT", "PGSSLROOTCERT")]
] + [{"name": "PGPASSWORD", "valueFrom": {"secretKeyRef": {"name": "mosemo-secrets", "key": "DB_PASSWORD"}}}]
print(json.dumps({
    "apiVersion": "v1", "kind": "Pod",
    "metadata": {"name": name, "namespace": namespace, "labels": {"app": "step5-tools"}},
    "spec": {
        "restartPolicy": "Never", "activeDeadlineSeconds": 600, "automountServiceAccountToken": False,
        "nodeSelector": {"kubernetes.io/arch": "amd64", "pool": "loadgen"},
        "tolerations": [{"key": "dedicated", "operator": "Equal", "value": "loadgen", "effect": "NoSchedule"}],
        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                            "seccompProfile": {"type": "RuntimeDefault"}},
        "containers": [{
            "name": "check", "image": image, "imagePullPolicy": "IfNotPresent",
            "command": ["bash", "-c", script],
            "envFrom": [{"configMapRef": {"name": "mosemo-config"}}, {"secretRef": {"name": "mosemo-secrets"}}],
            "env": env,
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "500m", "memory": "256Mi"}},
            "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
            "volumeMounts": [{"name": "rds-ca", "mountPath": "/etc/rds-ca", "readOnly": True}],
        }],
        "volumes": [{"name": "rds-ca", "configMap": {"name": "mosemo-rds-ca"}}],
    },
}))
PY
  while ((elapsed < 600)); do
    phase=$(k get pod "$name" -o jsonpath='{.status.phase}')
    [[ $phase == Succeeded || $phase == Failed ]] && break
    sleep 5
    ((elapsed += 5))
  done
  k get pod "$name" -o json >"$raw/$name.pod.json"
  k logs "$name" 2>/dev/null || true
  k delete pod "$name" --wait=false >/dev/null
}

pod_field() { # pod_field NAME PYTHON_EXPRESSION_ON_p
  python3 -c "import json,sys; p=json.load(open(sys.argv[1])); print($2)" "$raw/$1.pod.json"
}

step "P7 application state"
# kubectl get deployment -o jsonpath status.conditions[Available]
available() { [[ $(k get deployment "$1" -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null) == True ]]; }
check "P7 mosemo-server Available" available mosemo-server
check "P7 mosemo-consumer Available" available mosemo-consumer
no_batch() { [[ -z $(k get deployment mosemo-batch --ignore-not-found -o name) ]]; }
check "P7 mosemo-batch absent (measurement profile)" no_batch

step "P2 image and alembic head"
# kubectl get deployment mosemo-server -> image repo@sha256; aws ecr describe-images -> tags (commit)
image=$(k get deployment mosemo-server -o jsonpath='{.spec.template.spec.containers[0].image}')
image_uri=""
image_digest=""
image_commit=""
alembic_head=""
if [[ $image =~ ^([0-9]{12}\.dkr\.ecr\.$AWS_REGION\.amazonaws\.com/mosemo-lab-server)@(sha256:[0-9a-f]{64})$ ]]; then
  image_uri=${BASH_REMATCH[1]}
  image_digest=${BASH_REMATCH[2]}
  pass "P2 deployed image is pinned by digest: $image"
  tags=$(awscli ecr describe-images --repository-name mosemo-lab-server --image-ids "imageDigest=$image_digest" \
    --query 'imageDetails[0].imageTags' --output text)
  image_commit=$(tr -s ' \t' '\n' <<<"$tags" | grep -E '^[0-9a-f]{40}$' | head -n1 || true)
  if [[ -n $image_commit ]]; then pass "P2 image commit $image_commit"; else failed "P2 no commit tag on $image_digest (tags: $tags)"; fi
  # alembic heads inside the same image, on the loadgen node
  alembic_head=$(run_tool_pod step5-alembic "$image" 'alembic heads' | awk 'NF {print $1; exit}')
  if [[ $alembic_head == "$expected_head" ]]; then
    pass "P2 alembic head $alembic_head"
  else
    failed "P2 alembic head '${alembic_head:-none}' != expected $expected_head"
  fi
else
  failed "P2 mosemo-server image is not this account's mosemo-lab-server pinned by digest: $image"
fi

step "P3-P6 one-off check Pod (postgres:18.3 on the loadgen node)"
# Runs inside the check Pod; expansions happen there, not here.
# shellcheck disable=SC2016
ready_script='
db() { psql -XAt -v ON_ERROR_STOP=1 "$@"; }
echo "shared_preload_libraries=$(db -c "SHOW shared_preload_libraries" 2>/dev/null)"
echo "track_io_timing=$(db -c "SHOW track_io_timing" 2>/dev/null)"
if db -c "BEGIN" -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements" -c "ROLLBACK" >/dev/null 2>&1; then
  echo create_extension=yes
else
  echo create_extension=no
fi
if db -c "SELECT 1" >/dev/null 2>&1; then echo rds_5432=yes; else echo rds_5432=no; fi
http_status() {
  exec 3<>"/dev/tcp/mosemo-server.$POD_NAMESPACE.svc.cluster.local/80" || return
  printf "GET /openapi.json HTTP/1.0\r\nHost: mosemo-server\r\n\r\n" >&3
  read -r -t 10 _ code _ <&3
  echo "$code"
}
echo "api_http=$(http_status 2>/dev/null)"
'
ready_script="POD_NAMESPACE=$NAMESPACE
$ready_script"
ready_out=$(run_tool_pod step5-ready postgres:18.3 "$ready_script")
printf '%s\n' "$ready_out" >"$raw/step5-ready.log"
value() { sed -n "s/^$1=//p" <<<"$ready_out" | tr -d '\r'; }
info "P3 shared_preload_libraries=$(value shared_preload_libraries)"
info "P3 track_io_timing=$(value track_io_timing)"
check "P3 CREATE EXTENSION pg_stat_statements possible (rolled back)" test "$(value create_extension)" = yes
check "P4 RDS 5432 reachable from the loadgen node" test "$(value rds_5432)" = yes
check "P4 mosemo-server Service /openapi.json HTTP 200" test "$(value api_http)" = 200
# kubectl get pod -o json -> status.containerStatuses[0].imageID (a pulled image)
ready_image_id=$(pod_field step5-ready 'p["status"].get("containerStatuses", [{}])[0].get("imageID", "")')
check "P5 postgres:18.3 pulled (${ready_image_id:-no image})" test -n "$ready_image_id"
ready_node=$(pod_field step5-ready 'p["spec"].get("nodeName", "")')
loadgen_nodes=$(kubectl get nodes -l pool=loadgen -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
check "P6 exactly one loadgen node" test "$(grep -c . <<<"$loadgen_nodes")" -eq 1
check "P6 check Pod ran on the loadgen node ($ready_node)" test "$ready_node" = "$loadgen_nodes"
api_nodes=$(k get pods -l app=mosemo-server -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}')
api_elsewhere() { [[ -n $api_nodes ]] && ! grep -qx "$ready_node" <<<"$api_nodes"; }
check "P6 API Pods on other nodes ($(tr '\n' ' ' <<<"$api_nodes"))" api_elsewhere

# kubectl get node/pods -> loadgen allocatable minus DaemonSet requests (room for the measurement Pod)
kubectl get nodes -o json >"$raw/nodes.json"
kubectl get pods -A -o json >"$raw/pods.json"
room=$(python3 - "$raw/nodes.json" "$raw/pods.json" "$loadgen_nodes" <<'PY'
import json, re, sys
def qty(v):
    n, unit = re.fullmatch(r"([0-9.]+)([a-zA-Z]*)", v).groups()
    return float(n) * {"": 1, "m": 0.001, "k": 1e3, "M": 1e6, "G": 1e9, "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}[unit]
nodes, pods, name = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), sys.argv[3]
node = next(n for n in nodes["items"] if n["metadata"]["name"] == name)
cpu, mem = qty(node["status"]["allocatable"]["cpu"]), qty(node["status"]["allocatable"]["memory"])
for p in pods["items"]:
    if p["spec"].get("nodeName") != name or p["status"].get("phase") in ("Succeeded", "Failed"):
        continue
    if not any(o.get("kind") == "DaemonSet" for o in p["metadata"].get("ownerReferences", [])):
        continue
    for c in p["spec"]["containers"]:
        req = c.get("resources", {}).get("requests", {})
        cpu -= qty(req.get("cpu", "0"))
        mem -= qty(req.get("memory", "0"))
print(int(cpu * 1000), int(mem / 1024**2))
PY
) || room="0 0"
read -r room_cpu_m room_mem_mi <<<"$room"
check "P6 loadgen room after DaemonSets ${room_cpu_m}m CPU >= 700m" test "$room_cpu_m" -ge 700
check "P6 loadgen room after DaemonSets ${room_mem_mi}Mi memory >= 768Mi" test "$room_mem_mi" -ge 768

step "P1 measurement operator permissions (10-minute token)"
probe_kubeconfig="$SESSION_DIR/rbac-probe.kubeconfig"
issue_operator_kubeconfig "$probe_kubeconfig" 10m >/dev/null
while read -r expected verb resource subresource; do
  [[ -z $expected || $expected == \#* ]] && continue
  if [[ -n $subresource ]]; then
    answer=$(kubectl --kubeconfig "$probe_kubeconfig" -n "$NAMESPACE" auth can-i "$verb" "$resource" --subresource="$subresource" 2>/dev/null || true)
  else
    answer=$(kubectl --kubeconfig "$probe_kubeconfig" -n "$NAMESPACE" auth can-i "$verb" "$resource" 2>/dev/null || true)
  fi
  check "P1 can-i $verb $resource${subresource:+ ($subresource)} = $expected" test "$answer" = "$expected"
done <"$(dirname "$0")/measurement-rbac-matrix.txt"
rm -f "$probe_kubeconfig"
info "P1 measurement kubeconfig is issued by measurement-lock.sh start: $MEASUREMENT_KUBECONFIG"

step "P8 CPU credit balances and P9 environment manifest"
# aws eks describe-cluster / list-addons / describe-addon -> eks.version, eks.addons (none listed fails P9)
awscli eks describe-cluster --name "$CLUSTER_NAME" >"$raw/cluster.json"
if addons=$(awscli eks list-addons --cluster-name "$CLUSTER_NAME" --query 'addons[]' --output text) &&
  [[ -n ${addons//[[:space:]]/} && $addons != None ]]; then
  for addon in $addons; do
    awscli eks describe-addon --cluster-name "$CLUSTER_NAME" --addon-name "$addon" >"$raw/addon-$addon.json"
  done
  pass "P9 EKS add-ons: $(xargs <<<"$addons")"
else
  failed "P9 eks list-addons failed or listed no add-ons"
fi
# aws eks list-nodegroups / describe-nodegroup -> nodegroups; describe-subnets -> nodegroups[].availability_zones
for group in $(awscli eks list-nodegroups --cluster-name "$CLUSTER_NAME" --query 'nodegroups[]' --output text); do
  awscli eks describe-nodegroup --cluster-name "$CLUSTER_NAME" --nodegroup-name "$group" >"$raw/nodegroup-$group.json"
done
subnet_ids=$(python3 -c '
import glob, json, os, sys
print(" ".join(sorted({s for p in glob.glob(os.path.join(sys.argv[1], "nodegroup-*.json"))
                       for s in json.load(open(p))["nodegroup"]["subnets"]})))' "$raw")
# shellcheck disable=SC2086 # One argument per subnet ID.
awscli ec2 describe-subnets --subnet-ids $subnet_ids >"$raw/subnets.json"
# kubectl get nodes (providerID) + aws ec2 describe-instances -> instance ID, AZ, launch time
instance_ids=$(python3 -c '
import json, sys
for n in json.load(open(sys.argv[1]))["items"]:
    print(n["spec"]["providerID"].rsplit("/", 1)[-1])' "$raw/nodes.json")
# shellcheck disable=SC2086 # One argument per instance ID.
awscli ec2 describe-instances --instance-ids $instance_ids >"$raw/instances.json"
# aws ec2 describe-instance-credit-specifications -> nodegroups[].credit of burstable (T) instances.
# The launch template does not show it: the AWS provider drops credit_specification without an instance type there.
burstable_ids=$(python3 -c '
import json, re, sys
print(" ".join(i["InstanceId"] for r in json.load(open(sys.argv[1]))["Reservations"] for i in r["Instances"]
               if re.match(r"t[0-9]", i["InstanceType"])))' "$raw/instances.json")
if [[ -n $burstable_ids ]]; then
  # shellcheck disable=SC2086 # One argument per instance ID.
  awscli ec2 describe-instance-credit-specifications --instance-ids $burstable_ids >"$raw/instance-credits.json"
else
  echo '{"InstanceCreditSpecifications": []}' >"$raw/instance-credits.json"
fi
# aws rds describe-db-instances -> rds.* (engine, class, storage, parameter group, AZ)
awscli rds describe-db-instances --db-instance-identifier "$CLUSTER_NAME" >"$raw/rds.json"
rds_class=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["DBInstances"][0]["DBInstanceClass"])' "$raw/rds.json")
# kubectl get limitrange -> limitrange_mosemo; kubectl get pods -l app=mosemo-server -> api_pods
k get limitrange mosemo-container-resources -o json >"$raw/limitrange.json"
k get pods -l app=mosemo-server -o json >"$raw/api-pods.json"
# aws cloudwatch get-metric-data -> latest five-minute CPUCreditBalance of app/loadgen nodes and a db.t* RDS (no verdict)
python3 - "$raw/nodes.json" "$CLUSTER_NAME" "$rds_class" >"$raw/credit-queries.json" <<'PY'
import json, re, sys
nodes, rds, rds_class = json.load(open(sys.argv[1]))["items"], sys.argv[2], sys.argv[3]
queries = []
for n in nodes:
    pool = n["metadata"]["labels"].get("pool", "")
    if pool in ("app", "loadgen"):
        iid = n["spec"]["providerID"].rsplit("/", 1)[-1]
        queries.append({"Id": re.sub(r"[^a-z0-9_]", "_", f"{pool}_{iid}".lower()), "Label": f"{pool} {iid}", "MetricStat": {
            "Metric": {"Namespace": "AWS/EC2", "MetricName": "CPUCreditBalance", "Dimensions": [{"Name": "InstanceId", "Value": iid}]},
            "Period": 300, "Stat": "Average"}})
if rds_class.startswith("db.t"):
    queries.append({"Id": "rds", "Label": f"rds {rds}", "MetricStat": {
        "Metric": {"Namespace": "AWS/RDS", "MetricName": "CPUCreditBalance", "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": rds}]},
        "Period": 300, "Stat": "Average"}})
print(json.dumps(queries))
PY
awscli cloudwatch get-metric-data --metric-data-queries "file://$raw/credit-queries.json" \
  --start-time "$(date -u -v-30M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '-30 min' +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$raw/credits.json"
python3 -c '
import json, sys
for r in json.load(open(sys.argv[1]))["MetricDataResults"]:
    latest = max(zip(r["Timestamps"], r["Values"]), default=None)
    print("P8 CPUCreditBalance " + r["Label"] + " = " + (f"{latest[1]} at {latest[0]}" if latest else "no datapoint yet"))
' "$raw/credits.json" | while read -r line; do info "$line"; done

python3 - "$raw" "$MEASUREMENT_DIR" "$AWS_REGION" "$image_uri" "$image_digest" "$image_commit" "$alembic_head" <<'PY'
import datetime, glob, hashlib, json, os, re, sys
raw, out, region, uri, digest, commit, head = sys.argv[1:]
load = lambda name: json.load(open(os.path.join(raw, name)))
burstable = lambda instance_type: re.match(r"t[0-9]", instance_type) is not None
nodes = load("nodes.json")["items"]
instance_id = lambda n: n["spec"]["providerID"].rsplit("/", 1)[-1]
instances = {i["InstanceId"]: i for r in load("instances.json")["Reservations"] for i in r["Instances"]}
credits = {c["InstanceId"]: c["CpuCredits"] for c in load("instance-credits.json")["InstanceCreditSpecifications"]}
subnet_zone = {s["SubnetId"]: s["AvailabilityZone"] for s in load("subnets.json")["Subnets"]}
node_zone = {n["metadata"]["name"]: n["metadata"]["labels"].get("topology.kubernetes.io/zone", "") for n in nodes}
db = load("rds.json")["DBInstances"][0]
limits = load("limitrange.json")["spec"]["limits"][0]
groups = []
for path in sorted(glob.glob(os.path.join(raw, "nodegroup-*.json"))):
    g = json.load(open(path))["nodegroup"]
    # The credit mode of the group's running T instances ("none" for non-burstable types).
    members = [instance_id(n) for n in nodes if n["metadata"]["labels"].get("eks.amazonaws.com/nodegroup") == g["nodegroupName"]]
    credit = (",".join(sorted({credits.get(i, "unknown") for i in members})) or "unknown"
              if any(burstable(t) for t in g["instanceTypes"]) else "none")
    groups.append({"name": g["nodegroupName"], "instance_type": ",".join(g["instanceTypes"]),
                   "ami": f'{g["amiType"]} {g.get("releaseVersion", "")}'.strip(),
                   "credit": credit,
                   "availability_zones": sorted({subnet_zone[s] for s in g["subnets"]}),
                   "desired": g["scalingConfig"]["desiredSize"], "taints": g.get("taints", [])})
manifest = {
    "captured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "region": region,
    "profile": "measurement",
    "terraform": load("../terraform.json"),
    "eks": {"version": load("cluster.json")["cluster"]["version"],
            "addons": {json.load(open(p))["addon"]["addonName"]: json.load(open(p))["addon"]["addonVersion"]
                       for p in sorted(glob.glob(os.path.join(raw, "addon-*.json")))}},
    "nodegroups": groups,
    "nodes": [{"name": n["metadata"]["name"], "pool": n["metadata"]["labels"].get("pool", ""),
               "instance_id": (iid := instance_id(n)),
               "instance_type": instances[iid]["InstanceType"],
               "availability_zone": instances[iid]["Placement"]["AvailabilityZone"],
               "launch_time": instances[iid]["LaunchTime"]} for n in nodes],
    "api_pods": [{"name": p["metadata"]["name"], "node": p["spec"].get("nodeName", ""),
                  "availability_zone": node_zone.get(p["spec"].get("nodeName", ""), "")}
                 for p in load("api-pods.json")["items"]],
    "rds": {"engine_version": db["EngineVersion"], "class": db["DBInstanceClass"],
            "storage": f'{db["StorageType"]} {db["AllocatedStorage"]}GB',
            "parameter_group": db["DBParameterGroups"][0]["DBParameterGroupName"],
            # RDS runs db.t* classes in Unlimited mode and has no API field for it; other classes have no credits.
            "credit": "unlimited" if db["DBInstanceClass"].startswith("db.t") else "none",
            "availability_zone": db["AvailabilityZone"]},
    "app_image": {"uri": uri, "digest": digest, "commit": commit, "alembic_head": head},
    "limitrange_mosemo": {"default": limits.get("default", {}), "default_request": limits.get("defaultRequest", {}),
                          "max": limits.get("max", {})},
}
data = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
open(os.path.join(out, "manifest.json"), "wb").write(data)
open(os.path.join(out, "manifest.sha256"), "w").write(hashlib.sha256(data).hexdigest() + "  manifest.json\n")
PY
pass "P9 manifest $MEASUREMENT_DIR/manifest.json sha256 $(cut -d' ' -f1 "$MEASUREMENT_DIR/manifest.sha256")"

if ((failures > 0)); then
  printf 'RESULT FAIL (%s)\n' "$failures" | tee -a "$report"
  exit 1
fi
printf 'RESULT PASS\n' | tee -a "$report"
echo "Next: send ready.txt, manifest.json and manifest.sha256, then measurement-lock.sh start POD_NAME EXPECTED_END_UTC"

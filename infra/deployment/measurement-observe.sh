#!/usr/bin/env bash
# Measurement-side observation that CloudWatch does not give, written to $SESSION_DIR/measurement/observe/.
#   start  After measurement-lock.sh start. Creates the lab-dbstat Pod (kafka node, pg_stat_database every 30s)
#          and a background sampler that appends API and consumer cgroup cpu.stat and memory.current every 30s
#          to cpu-stat.csv. The sampler stops by itself once the lock is gone.
#   stop   After measurement-lock.sh end. Stops the sampler, saves the lab-dbstat log as pg-stat-database.csv,
#          deletes the Pod and exports extra 1-minute RDS metrics for the window to rds-extra.csv.
# No kubectl top: the lab has no metrics-server.
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

OBSERVE_DIR="$MEASUREMENT_DIR/observe"
PID_FILE="$OBSERVE_DIR/sampler.pid"
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd -P)/$(basename "$0")"

# The PID file alone is not trusted: the process must still be this script's sampler.
sampler_pid() {
  local pid
  [[ -f $PID_FILE ]] || return 1
  pid=$(cat "$PID_FILE")
  [[ $pid =~ ^[0-9]+$ ]] && ps -p "$pid" -o command= 2>/dev/null | grep -q "measurement-observe.sh sample" || return 1
  echo "$pid"
}

start() {
  [[ -f $LOCK_FILE ]] || fail "no measurement lock at $LOCK_FILE; run measurement-lock.sh start first"
  check_session_dir
  require_tools kubectl
  ! sampler_pid >/dev/null || fail "a sampler is already running (PID $(cat "$PID_FILE"))"
  check_cluster_access

  mkdir -p "$OBSERVE_DIR"
  step "lab-dbstat Pod"
  kubectl apply -f "$K8S_DIR/lab-dbstat-pod.yaml" >/dev/null
  kubectl -n "$NAMESPACE" wait --for=condition=Ready pod/lab-dbstat --timeout=3m >/dev/null \
    || fail "lab-dbstat is not Ready; inspect with: kubectl -n $NAMESPACE describe pod lab-dbstat"

  step "cgroup sampler"
  nohup bash "$SCRIPT_PATH" sample >>"$OBSERVE_DIR/observe.log" 2>&1 </dev/null &
  echo $! >"$PID_FILE"
  echo "sampler PID $(cat "$PID_FILE") -> $OBSERVE_DIR/cpu-stat.csv"
  echo "Next, after measurement-lock.sh end: bash infra/deployment/measurement-observe.sh stop"
}

# Background loop started by `start`; one row per API or consumer Pod every 30 seconds.
sample() {
  # A failed kubectl call loses one row, not the whole sampler.
  set +e
  local csv="$OBSERVE_DIR/cpu-stat.csv"
  [[ -f $csv ]] || echo "ts,pod,node,usage_usec,nr_periods,nr_throttled,throttled_usec,memory_current_bytes" >"$csv"
  echo "sampler started $(date -u +%FT%TZ)"
  while [[ -f $LOCK_FILE ]]; do
    sample_once "$csv" &
    sleep 30
    wait
  done
  echo "sampler stopped $(date -u +%FT%TZ): measurement lock removed"
}

sample_once() {
  local csv=$1 ts pod node stat
  ts=$(date -u +%FT%TZ)
  kubectl -n "$NAMESPACE" get pods -l 'app in (mosemo-server,mosemo-consumer)' \
    -o jsonpath='{range .items[*]}{.metadata.name} {.spec.nodeName}{"\n"}{end}' | while read -r pod node; do
    # shellcheck disable=SC2016 # Expanded by the shell inside the Pod.
    stat=$(kubectl -n "$NAMESPACE" exec "$pod" -- sh -c \
      'cat /sys/fs/cgroup/cpu.stat; echo memory_current $(cat /sys/fs/cgroup/memory.current)' 2>/dev/null) || continue
    v() { awk -v k="$1" '$1 == k { print $2 }' <<<"$stat"; }
    echo "$ts,$pod,$node,$(v usage_usec),$(v nr_periods),$(v nr_throttled),$(v throttled_usec),$(v memory_current)" >>"$csv"
  done
}

stop() {
  check_session_dir
  [[ ! -e $LOCK_FILE ]] || fail "measurement lock $LOCK_FILE exists; run measurement-lock.sh end first"
  [[ -f $MEASUREMENT_DIR/window ]] || fail "missing $MEASUREMENT_DIR/window; run measurement-lock.sh end first"
  local start end pid
  start=$(sed -n 's/^start=//p' "$MEASUREMENT_DIR/window")
  end=$(sed -n 's/^end=//p' "$MEASUREMENT_DIR/window")
  [[ -n $start && -n $end ]] || fail "$MEASUREMENT_DIR/window needs start= and end= lines"
  require_tools kubectl aws python3
  check_cluster_access
  check_aws_login
  mkdir -p "$OBSERVE_DIR"

  step "Sampler"
  if pid=$(sampler_pid); then
    kill "$pid"
    echo "stopped sampler PID $pid"
  else
    echo "no running sampler"
  fi
  rm -f "$PID_FILE"

  step "lab-dbstat"
  if kubectl -n "$NAMESPACE" get pod lab-dbstat >/dev/null 2>&1; then
    {
      echo "now,xact_commit,xact_rollback,numbackends,tup_inserted,tup_updated,blks_hit,blks_read"
      kubectl -n "$NAMESPACE" logs lab-dbstat
    } >"$OBSERVE_DIR/pg-stat-database.csv"
    kubectl -n "$NAMESPACE" delete pod lab-dbstat --wait=true >/dev/null
    echo "pg_stat_database: $OBSERVE_DIR/pg-stat-database.csv ($(($(wc -l <"$OBSERVE_DIR/pg-stat-database.csv") - 1)) rows)"
  elif [[ -f $OBSERVE_DIR/pg-stat-database.csv ]]; then
    echo "lab-dbstat already deleted; keeping $OBSERVE_DIR/pg-stat-database.csv"
  else
    fail "lab-dbstat Pod not found and no pg-stat-database.csv; the DB statistics are lost"
  fi

  step "RDS GetMetricData $start .. $end"
  # The infrastructure role has GetMetricData but not GetMetricStatistics.
  local queries="$OBSERVE_DIR/rds-extra-queries.json"
  python3 - "$CLUSTER_NAME" >"$queries" <<'PY'
import json, sys
rds = sys.argv[1]
metrics = [("DatabaseConnections", "Average"), ("DatabaseConnections", "Maximum"), ("CPUUtilization", "Average"),
           ("ReadIOPS", "Average"), ("WriteIOPS", "Average"), ("DiskQueueDepth", "Average"),
           ("WriteLatency", "Average"), ("FreeableMemory", "Average")]
print(json.dumps([{"Id": f"m{i}", "Label": f"{metric},{stat}", "ReturnData": True, "MetricStat": {
    "Metric": {"Namespace": "AWS/RDS", "MetricName": metric,
               "Dimensions": [{"Name": "DBInstanceIdentifier", "Value": rds}]},
    "Period": 60, "Stat": stat}} for i, (metric, stat) in enumerate(metrics)]))
PY
  aws cloudwatch get-metric-data --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION" \
    --metric-data-queries "file://$queries" --start-time "$start" --end-time "$end" \
    --scan-by TimestampAscending >"$OBSERVE_DIR/rds-extra-raw.json"
  python3 - "$OBSERVE_DIR/rds-extra-raw.json" "$OBSERVE_DIR/rds-extra.csv" <<'PY'
import csv, json, sys
results = json.load(open(sys.argv[1]))["MetricDataResults"]
rows = 0
with open(sys.argv[2], "w", newline="") as out:
    writer = csv.writer(out)
    writer.writerow(["metric", "stat", "timestamp", "value"])
    for r in results:
        metric, stat = r["Label"].split(",")
        if r.get("StatusCode") != "Complete":
            print(f"warning: {r['Label']} status {r.get('StatusCode')}", file=sys.stderr)
        if not r["Timestamps"]:
            print(f"warning: no datapoints for {r['Label']}", file=sys.stderr)
        for ts, value in sorted(zip(r["Timestamps"], r["Values"])):
            writer.writerow([metric, stat, ts, value])
            rows += 1
print(f"{rows} datapoints")
PY
  echo "rds: $OBSERVE_DIR/rds-extra.csv"
}

command=${1:-}
case $command in
  start) start ;;
  stop) stop ;;
  sample) sample ;;
  *) fail "usage: measurement-observe.sh start | stop" ;;
esac

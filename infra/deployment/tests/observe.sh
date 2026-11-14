#!/usr/bin/env bash
# Offline checks for measurement-observe.sh with fake kubectl and aws first on PATH (calls recorded in $FAKE_LOG).
# Nothing reaches AWS or a cluster. Usage: bash infra/deployment/tests/observe.sh
set -euo pipefail

DEPLOY_DIR=$(cd "$(dirname "$0")/.." && pwd -P)
WORK=$(mktemp -d)
FAKE_BIN="$WORK/bin"
mkdir -p "$FAKE_BIN"
cleanup() {
  [[ -n ${MOSEMO_SESSION_DIR:-} && -f $MOSEMO_SESSION_DIR/measurement/observe/sampler.pid ]] &&
    kill "$(cat "$MOSEMO_SESSION_DIR/measurement/observe/sampler.pid")" 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

cat >"$FAKE_BIN/fake" <<'FAKE'
#!/bin/bash
cmd=$(basename "$0")
printf '%s|%s\n' "$cmd" "$*" >>"$FAKE_LOG"
args=" $* "
case $cmd in
  kubectl)
    case $args in
      *" get pods -l app in (mosemo-server,mosemo-consumer) "*) printf 'mosemo-server-a node-app-1\nmosemo-consumer-b node-app-2\n' ;;
      *" exec "*) printf 'usage_usec 100\nuser_usec 60\nnr_periods 5\nnr_throttled 1\nthrottled_usec 20\nmemory_current 1234\n' ;;
      *" apply -f "*lab-dbstat-pod.yaml*) touch "$FAKE_STATE/dbstat" ;;
      *" get pod lab-dbstat "*) [[ -f $FAKE_STATE/dbstat ]] || exit 1 ;;
      *" logs lab-dbstat "*) printf '2026-11-20 09:00:00+00,10,0,5,7,3,100,2\n2026-11-20 09:00:30+00,20,0,6,9,4,150,2\n' ;;
      *" delete pod lab-dbstat "*) rm -f "$FAKE_STATE/dbstat" ;;
    esac
    ;;
  aws)
    case $args in
      *" sts get-caller-identity "*) echo "arn:aws:sts::123456789012:assumed-role/MosemoInfrastructureRole/test" ;;
      *" cloudwatch get-metric-data "*)
        echo '{"MetricDataResults": [
          {"Label": "DatabaseConnections,Maximum", "StatusCode": "Complete",
           "Timestamps": ["2026-11-20T09:01:00Z", "2026-11-20T09:00:00Z"], "Values": [12.0, 10.0]},
          {"Label": "WriteLatency,Average", "StatusCode": "Complete", "Timestamps": [], "Values": []}]}'
        ;;
    esac
    ;;
esac
exit 0
FAKE
chmod +x "$FAKE_BIN/fake"
for tool in aws kubectl; do
  ln -s fake "$FAKE_BIN/$tool"
done
export PATH="$FAKE_BIN:$PATH"

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
new_case() {
  CASE="$WORK/case$((passed + failed))"
  export MOSEMO_SESSION_DIR="$CASE/session" FAKE_STATE="$CASE/fake" FAKE_LOG="$CASE/calls.log"
  mkdir -p "$MOSEMO_SESSION_DIR" "$FAKE_STATE"
  chmod 700 "$MOSEMO_SESSION_DIR"
  echo "apiVersion: v1" >"$MOSEMO_SESSION_DIR/kubeconfig"
  echo measurement >"$MOSEMO_SESSION_DIR/profile"
  : >"$FAKE_LOG"
  OBSERVE="$MOSEMO_SESSION_DIR/measurement/observe"
}
lock() { echo "started_at=2026-11-20T09:00:00Z" >"$MOSEMO_SESSION_DIR/measurement.lock"; }
window() {
  mkdir -p "$MOSEMO_SESSION_DIR/measurement"
  printf 'start=2026-11-20T09:00:00Z\nend=2026-11-20T21:00:00Z\n' >"$MOSEMO_SESSION_DIR/measurement/window"
}
run() { # sets STATUS and OUTPUT
  set +e
  OUTPUT=$(bash "$DEPLOY_DIR/measurement-observe.sh" "$@" 2>&1 </dev/null)
  STATUS=$?
  set -e
}
refused() { # refused LABEL PATTERN
  if ((STATUS != 0)) && grep -q "$2" <<<"$OUTPUT" && [[ ! -s $FAKE_LOG ]]; then
    ok "$1"
  else
    not_ok "$1 (exit $STATUS, $(wc -l <"$FAKE_LOG" | tr -d ' ') calls)" "$OUTPUT"
  fi
}

echo "a. refusals before any call"
new_case
run start
refused "a start without the measurement lock" "no measurement lock"
new_case
lock
window
run stop
refused "a stop while the lock still exists" "measurement lock .* exists"
new_case
run stop
refused "a stop without measurement/window" "missing .*/window"

echo "b. start, then stop after the lock ends"
new_case
lock
run start
pid=$(cat "$OBSERVE/sampler.pid" 2>/dev/null || true)
for ((i = 0; i < 50; i++)); do
  [[ -f $OBSERVE/cpu-stat.csv && $(wc -l <"$OBSERVE/cpu-stat.csv") -ge 3 ]] && break
  sleep 0.2
done
if ((STATUS == 0)) && grep -q 'kubectl|apply -f .*/infra/kubernetes/lab-dbstat-pod.yaml' "$FAKE_LOG" &&
  [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null &&
  [[ $(head -n1 "$OBSERVE/cpu-stat.csv") == "ts,pod,node,usage_usec,nr_periods,nr_throttled,throttled_usec,memory_current_bytes" ]] &&
  grep -qE '^[0-9T:-]+Z,mosemo-server-a,node-app-1,100,5,1,20,1234$' "$OBSERVE/cpu-stat.csv" &&
  grep -qE ',mosemo-consumer-b,node-app-2,100,5,1,20,1234$' "$OBSERVE/cpu-stat.csv"; then
  ok "b start creates lab-dbstat and a running sampler that writes one cgroup row per Pod"
else
  not_ok "b start creates lab-dbstat and a running sampler (exit $STATUS)" "$OUTPUT"
fi
run start
if ((STATUS != 0)) && grep -q "already running" <<<"$OUTPUT"; then ok "b second start refuses"; else not_ok "b second start refuses (exit $STATUS)" "$OUTPUT"; fi
rm "$MOSEMO_SESSION_DIR/measurement.lock"
window
run stop
expected_rds="metric,stat,timestamp,value
DatabaseConnections,Maximum,2026-11-20T09:00:00Z,10.0
DatabaseConnections,Maximum,2026-11-20T09:01:00Z,12.0"
if ((STATUS == 0)) && ! kill -0 "$pid" 2>/dev/null && [[ ! -e $OBSERVE/sampler.pid ]] &&
  [[ $(head -n1 "$OBSERVE/pg-stat-database.csv") == "now,xact_commit,xact_rollback,numbackends,tup_inserted,tup_updated,blks_hit,blks_read" ]] &&
  [[ $(wc -l <"$OBSERVE/pg-stat-database.csv") -eq 3 ]] &&
  grep -q 'kubectl|-n mosemo delete pod lab-dbstat' "$FAKE_LOG" &&
  grep -q 'aws|cloudwatch get-metric-data .*--start-time 2026-11-20T09:00:00Z --end-time 2026-11-20T21:00:00Z' "$FAKE_LOG" &&
  [[ $(tr -d '\r' <"$OBSERVE/rds-extra.csv") == "$expected_rds" ]] &&
  python3 -c '
import json, sys
q = json.load(open(sys.argv[1]))
got = {(x["MetricStat"]["Metric"]["MetricName"], x["MetricStat"]["Stat"], x["MetricStat"]["Period"]) for x in q}
want = {(m, "Average", 60) for m in ["DatabaseConnections", "CPUUtilization", "ReadIOPS", "WriteIOPS",
        "DiskQueueDepth", "WriteLatency", "FreeableMemory"]} | {("DatabaseConnections", "Maximum", 60)}
assert got == want and len(q) == 8, got' "$OBSERVE/rds-extra-queries.json"; then
  ok "b stop kills the sampler, saves pg_stat_database with a header, deletes the Pod, exports the RDS window"
else
  not_ok "b stop kills the sampler, saves pg_stat_database, deletes the Pod, exports RDS (exit $STATUS)" "$OUTPUT"
fi
run stop
if ((STATUS == 0)) && grep -q "already deleted" <<<"$OUTPUT" && [[ $(wc -l <"$OBSERVE/pg-stat-database.csv") -eq 3 ]]; then
  ok "b a second stop keeps the saved pg_stat_database"
else
  not_ok "b a second stop keeps the saved pg_stat_database (exit $STATUS)" "$OUTPUT"
fi
new_case
window
run stop
if ((STATUS != 0)) && grep -q "lab-dbstat Pod not found" <<<"$OUTPUT"; then
  ok "b stop without lab-dbstat and without a saved CSV fails"
else
  not_ok "b stop without lab-dbstat and without a saved CSV fails (exit $STATUS)" "$OUTPUT"
fi

printf '\n%s passed, %s failed\n' "$passed" "$failed"
((failed == 0))

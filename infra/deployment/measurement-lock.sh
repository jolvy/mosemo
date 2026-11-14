#!/usr/bin/env bash
# Measurement lock. While $SESSION_DIR/measurement.lock exists, session-up, deploy, session-down and
# measurement-ready refuse to run, so nothing changes the lab during a measurement.
#   start POD_NAME EXPECTED_END_UTC  Before the measurement Pod exists. Records the loadgen node and issues
#                                    the step5-operator kubeconfig (measurement.kubeconfig) with a token valid
#                                    until 30 minutes after the expected end (at least 1 hour, at most 13 hours).
#   end [--invalid REASON]           Removes the lock and records measurement/window; writes measurement/invalid
#                                    with --invalid, otherwise removes an earlier one.
# Over 12 hours without an end signal: delete the measurement Pod, run `end --invalid`, tell the user.
# Never session-down without the user's instruction.
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

utc_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

start() {
  [[ $# -eq 2 ]] || fail "usage: measurement-lock.sh start POD_NAME EXPECTED_END_UTC (e.g. 2026-11-20T21:00:00Z)"
  local pod=$1 expected_end=$2 expected_epoch nodes node duration exp
  [[ $pod =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fail "invalid Pod name: $pod"
  expected_epoch=$(python3 -c '
import datetime, sys
end = datetime.datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
print(int(end.timestamp()))' "$expected_end") || fail "EXPECTED_END_UTC must look like 2026-11-20T21:00:00Z"
  check_measurement_lock
  check_session_dir
  [[ $(session_profile) == measurement ]] || fail "the session does not run the measurement profile"
  require_tools kubectl python3
  check_cluster_access

  # The node, not the Pod: the Pod does not exist yet and must land on the only loadgen node.
  nodes=$(kubectl get nodes -l pool=loadgen -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
  [[ $(grep -c . <<<"$nodes") -eq 1 ]] || fail "expected exactly one loadgen node, found: ${nodes:-none}"
  node=$nodes

  # Expected end + 30 minutes, clamped to 1-13 hours: no longer-lived credential than the measurement needs.
  duration=$((expected_epoch - $(date +%s) + 1800))
  ((duration >= 3600)) || duration=3600
  ((duration <= 46800)) || duration=46800
  exp=$(issue_operator_kubeconfig "$MEASUREMENT_KUBECONFIG" "${duration}s")
  if ((exp < expected_epoch)); then
    rm -f "$MEASUREMENT_KUBECONFIG"
    fail "token expires at $exp, before the expected end $expected_end ($expected_epoch)"
  fi

  mkdir -p "$MEASUREMENT_DIR"
  printf 'started_at=%s\nexpected_end=%s\npod=%s\nnode=%s\n' "$(utc_now)" "$expected_end" "$pod" "$node" >"$LOCK_FILE"
  echo "measurement locked until end; loadgen node $node"
  echo "measurement kubeconfig (token valid until $(date -u -r "$exp" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "@$exp" +%Y-%m-%dT%H:%M:%SZ)): $MEASUREMENT_KUBECONFIG"
}

end() {
  local invalid=""
  if [[ $# -gt 0 ]]; then
    [[ $1 == --invalid && $# -eq 2 && -n $2 ]] || fail "usage: measurement-lock.sh end [--invalid REASON]"
    invalid=$2
  fi
  [[ -f $LOCK_FILE ]] || fail "no measurement lock at $LOCK_FILE"
  local started_at
  started_at=$(sed -n 's/^started_at=//p' "$LOCK_FILE")
  [[ -n $started_at ]] || fail "$LOCK_FILE has no started_at"
  mkdir -p "$MEASUREMENT_DIR"
  printf 'start=%s\nend=%s\n' "$started_at" "$(utc_now)" >"$MEASUREMENT_DIR/window"
  if [[ -n $invalid ]]; then
    printf '%s\n' "$invalid" >"$MEASUREMENT_DIR/invalid"
    echo "measurement recorded as invalid: $invalid"
  else
    rm -f "$MEASUREMENT_DIR/invalid"
  fi
  rm -f "$LOCK_FILE"
  echo "measurement lock removed; window $(tr '\n' ' ' <"$MEASUREMENT_DIR/window")"
  echo "Next: bash infra/deployment/measurement-observe.sh stop, then bash infra/deployment/measurement-metrics.sh (both before session-down.sh)"
}

command=${1:-}
[[ $# -gt 0 ]] && shift
case $command in
  start) start "$@" ;;
  end) end "$@" ;;
  *) fail "usage: measurement-lock.sh start POD_NAME EXPECTED_END_UTC | end [--invalid REASON]" ;;
esac

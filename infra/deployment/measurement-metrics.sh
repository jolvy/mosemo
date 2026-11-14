#!/usr/bin/env bash
# Export CloudWatch metrics for the measurement window to $SESSION_DIR/measurement/metrics.csv.
# Run after measurement-lock.sh end and BEFORE session-down.sh (the destroyed instances keep their
# metrics, but the manifest's instance IDs and the window must already exist).
# RDS at 1 minute; EC2 CPUUtilization at 1 minute (detailed monitoring), credit metrics at 5 minutes (AWS fixed).
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

step "Preflight"
require_tools aws python3
check_session_dir
[[ -f $MEASUREMENT_DIR/window ]] || fail "missing $MEASUREMENT_DIR/window; run measurement-lock.sh end first"
[[ -f $MEASUREMENT_DIR/manifest.json ]] || fail "missing $MEASUREMENT_DIR/manifest.json (written by measurement-ready.sh)"
check_aws_login

start=$(sed -n 's/^start=//p' "$MEASUREMENT_DIR/window")
end=$(sed -n 's/^end=//p' "$MEASUREMENT_DIR/window")
[[ -n $start && -n $end ]] || fail "$MEASUREMENT_DIR/window needs start= and end= lines"

step "GetMetricData $start .. $end"
queries="$MEASUREMENT_DIR/metrics-queries.json"
python3 - "$MEASUREMENT_DIR/manifest.json" "$CLUSTER_NAME" >"$queries" <<'PY'
import json, sys
manifest, rds = json.load(open(sys.argv[1])), sys.argv[2]
def query(qid, label, namespace, dimension, value, metric, period):
    return {"Id": qid, "Label": label, "ReturnData": True, "MetricStat": {
        "Metric": {"Namespace": namespace, "MetricName": metric, "Dimensions": [{"Name": dimension, "Value": value}]},
        "Period": period, "Stat": "Average"}}
queries = []
for i, metric in enumerate(["CPUUtilization", "CPUCreditBalance", "CPUSurplusCreditsCharged", "FreeableMemory",
                            "ReadIOPS", "WriteIOPS", "ReadLatency", "DiskQueueDepth", "FreeStorageSpace"]):
    queries.append(query(f"rds{i}", f"rds,{rds},{metric},60", "AWS/RDS", "DBInstanceIdentifier", rds, metric, 60))
for n, node in enumerate(manifest["nodes"]):
    for i, (metric, period) in enumerate([("CPUUtilization", 60), ("CPUCreditBalance", 300), ("CPUCreditUsage", 300),
                                          ("CPUSurplusCreditBalance", 300), ("CPUSurplusCreditsCharged", 300)]):
        label = f'{node["pool"]},{node["instance_id"]},{metric},{period}'
        queries.append(query(f"ec2n{n}m{i}", label, "AWS/EC2", "InstanceId", node["instance_id"], metric, period))
print(json.dumps(queries))
PY
# The CLI follows NextToken and merges all pages.
aws cloudwatch get-metric-data --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION" \
  --metric-data-queries "file://$queries" --start-time "$start" --end-time "$end" \
  --scan-by TimestampAscending >"$MEASUREMENT_DIR/metrics-raw.json"

python3 - "$MEASUREMENT_DIR/metrics-raw.json" "$MEASUREMENT_DIR/metrics.csv" <<'PY'
import csv, json, sys
results = json.load(open(sys.argv[1]))["MetricDataResults"]
rows = 0
with open(sys.argv[2], "w", newline="") as out:
    writer = csv.writer(out)
    writer.writerow(["source", "resource", "metric", "period_seconds", "timestamp", "value"])
    for r in results:
        source, resource, metric, period = r["Label"].split(",")
        if r.get("StatusCode") != "Complete":
            print(f"warning: {r['Label']} status {r.get('StatusCode')}", file=sys.stderr)
        for ts, value in sorted(zip(r["Timestamps"], r["Values"])):
            writer.writerow([source, resource, metric, period, ts, value])
            rows += 1
        if not r["Timestamps"]:
            print(f"warning: no datapoints for {r['Label']}", file=sys.stderr)
print(f"{rows} datapoints")
PY
echo "metrics: $MEASUREMENT_DIR/metrics.csv"
echo "Next: bash infra/deployment/session-down.sh"

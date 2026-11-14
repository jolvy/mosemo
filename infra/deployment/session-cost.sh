#!/usr/bin/env bash
# Next day after a session: one Cost Explorer GetCostAndUsage call (us-east-1, costs $0.01) for DATE,
# grouped by service and record type, then usage and credit totals. Cost data lags up to 24 hours.
# Usage: session-cost.sh YYYY-MM-DD
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

[[ $# -eq 1 && $1 =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || fail "usage: session-cost.sh YYYY-MM-DD"
day=$1
next=$(python3 -c 'import datetime,sys; print(datetime.date.fromisoformat(sys.argv[1]) + datetime.timedelta(days=1))' "$day") \
  || fail "invalid date: $day"

step "Preflight"
require_tools aws python3
check_aws_login

step "Cost for $day (UTC)"
# Cost Explorer is served only from us-east-1.
aws ce get-cost-and-usage --profile "$AWS_PROFILE_NAME" --region us-east-1 \
  --time-period "Start=$day,End=$next" --granularity DAILY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE Type=DIMENSION,Key=RECORD_TYPE --output json | python3 -c '
import json, sys
totals = {}
for period in json.load(sys.stdin)["ResultsByTime"]:
    for group in period["Groups"]:
        service, record_type = group["Keys"]
        cost = group["Metrics"]["UnblendedCost"]
        amount, unit = float(cost["Amount"]), cost["Unit"]
        totals[record_type] = totals.get(record_type, 0.0) + amount
        print(f"{service:<55} {record_type:<14} {amount:>10.4f} {unit}")
print()
for record_type, amount in sorted(totals.items()):
    print(f"total {record_type:<14} {amount:>10.4f} USD")
usage, credit, net = totals.get("Usage", 0.0), totals.get("Credit", 0.0), sum(totals.values())
print(f"usage {usage:.4f} USD, credit {credit:.4f} USD, net {net:.4f} USD")'

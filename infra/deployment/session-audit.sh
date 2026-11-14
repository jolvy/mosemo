#!/usr/bin/env bash
# After session-down.sh: confirm nothing of the session is left in ap-southeast-2 and, when a measurement
# window exists, that nothing changed the lab during it. Exits 1 when anything remains.
# Foundation resources (ECR, ACM, ALB log bucket) are expected to remain and are not checked here.
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

step "Preflight"
require_tools terraform aws python3
check_session_dir
check_aws_login
tf_init

awscli() { aws "$@" --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION"; }
leftovers=0
report="$SESSION_DIR/audit.txt"
printf 'session-audit %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$report"
expect_none() { # expect_none LABEL OUTPUT
  if [[ -z $(tr -d ' \t\n' <<<"$2") ]]; then
    printf 'OK   %s: none\n' "$1" | tee -a "$report"
  else
    printf 'LEFT %s: %s\n' "$1" "$(tr '\t\n' '  ' <<<"$2")" | tee -a "$report"
    ((leftovers += 1))
  fi
}

step "Session state and resources"
expect_none "terraform state (mosemo/lab)" "$(tf state list)"
expect_none "EKS clusters" "$(awscli eks list-clusters --query 'clusters[]' --output text)"
expect_none "lab EC2 instances" "$(awscli ec2 describe-instances \
  --filters "Name=tag:eks:cluster-name,Values=$CLUSTER_NAME" \
  "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
expect_none "RDS instances" "$(awscli rds describe-db-instances --query 'DBInstances[].DBInstanceIdentifier' --output text)"
expect_none "RDS manual snapshots" "$(awscli rds describe-db-snapshots --snapshot-type manual \
  --query 'DBSnapshots[].DBSnapshotIdentifier' --output text)"
expect_none "load balancers" "$(awscli elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerName' --output text)"
expect_none "target groups" "$(awscli elbv2 describe-target-groups --query 'TargetGroups[].TargetGroupName' --output text)"
expect_none "lab EBS volumes (Kafka PVC)" "$(awscli ec2 describe-volumes \
  --filters "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=$NAMESPACE" --query 'Volumes[].VolumeId' --output text)"
expect_none "unattached EBS volumes" "$(awscli ec2 describe-volumes --filters Name=status,Values=available \
  --query 'Volumes[].VolumeId' --output text)"
expect_none "Elastic IPs" "$(awscli ec2 describe-addresses --query 'Addresses[].AllocationId' --output text)"
expect_none "lab VPCs" "$(awscli ec2 describe-vpcs --filters "Name=tag:Name,Values=$CLUSTER_NAME" --query 'Vpcs[].VpcId' --output text)"
expect_none "unattached ENIs" "$(awscli ec2 describe-network-interfaces --filters Name=status,Values=available \
  --query 'NetworkInterfaces[].NetworkInterfaceId' --output text)"

# Expected remains: kept on purpose (delete_automated_backups = false) and expire with the retention period.
backups=$(awscli rds describe-db-instance-automated-backups --query 'DBInstanceAutomatedBackups[].[DBInstanceIdentifier,Status]' --output text)
[[ -z $backups ]] || printf 'WARN retained RDS automated backups (expected): %s\n' "$(tr '\t\n' '  ' <<<"$backups")" | tee -a "$report"
# The tagging index lags deletions by minutes, so it only informs; the service checks above decide.
tagged=$(awscli resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=mosemo Key=Environment,Values=lab \
  --query 'ResourceTagMappingList[].ResourceARN' --output text | tr -s ' \t' '\n' \
  | grep -vE ':repository/mosemo-lab-server$|:certificate/|^arn:aws:s3:::mosemo-lab-alb-logs-' || true)
[[ -z $tagged ]] || printf 'INFO tagging index still lists (recheck in a few minutes): %s\n' "$(tr '\n' ' ' <<<"$tagged")" | tee -a "$report"

if [[ -f $MEASUREMENT_DIR/window ]]; then
  step "CloudTrail changes during the measurement window"
  start=$(sed -n 's/^start=//p' "$MEASUREMENT_DIR/window")
  end=$(sed -n 's/^end=//p' "$MEASUREMENT_DIR/window")
  changes=$(awscli cloudtrail lookup-events --start-time "$start" --end-time "$end" \
    --lookup-attributes AttributeKey=ReadOnly,AttributeValue=false --output json | python3 -c '
import json, sys
for event in json.load(sys.stdin).get("Events", []):
    arn = json.loads(event["CloudTrailEvent"]).get("userIdentity", {}).get("arn", "")
    if "/MosemoInfrastructureRole/" in arn or "/AccountFullAccessRole/" in arn:
        print(event["EventTime"], event["EventSource"], event["EventName"], arn)')
  expect_none "lab changes $start .. $end" "$changes"
fi

if ((leftovers > 0)); then
  printf 'RESULT FAIL (%s)\n' "$leftovers" | tee -a "$report"
  exit 1
fi
printf 'RESULT PASS\n' | tee -a "$report"

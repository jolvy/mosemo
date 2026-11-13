#!/usr/bin/env bash
# End a work session: Kafka StatefulSet and PVC -> retained EBS deleted by volume ID and confirmed ->
# skip_final_snapshot applied to state -> RDS deletion protection lifted -> ALB deletion protection lifted ->
# session stack destroyed. Configuration keeps production protection; only this procedure lifts it.
# Session data is not kept. The long-lived ../terraform/foundation stack (ECR, ACM, ALB log bucket) is never touched.
# shellcheck source=session-lib.sh source-path=SCRIPTDIR
source "$(dirname "$0")/session-lib.sh"

step "Preflight"
require_tools terraform aws kubectl python3
check_session_dir
check_secrets_file
check_cloudflare_token
check_aws_login
load_db_password
tf_init

ec2() { aws ec2 "$@" --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION"; }
rds() { aws rds "$@" --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION"; }
elbv2() { aws elbv2 "$@" --profile "$AWS_PROFILE_NAME" --region "$AWS_REGION"; }

# The final snapshot name is fixed; a leftover one is someone's data, so stop before touching anything.
if snapshot=$(rds describe-db-snapshots --db-snapshot-identifier "$FINAL_SNAPSHOT_ID" \
  --query 'DBSnapshots[0].Status' --output text 2>&1); then
  fail "RDS snapshot $FINAL_SNAPSHOT_ID already exists ($snapshot). Restore or delete it deliberately, then rerun session-down.sh"
elif [[ $snapshot != *DBSnapshotNotFound* ]]; then
  fail "cannot check for RDS snapshot $FINAL_SNAPSHOT_ID: $snapshot"
fi
echo "no leftover RDS snapshot $FINAL_SNAPSHOT_ID"

step "Kafka StatefulSet, PVC and retained EBS"
if tf state list | grep -x 'aws_eks_cluster.main' >/dev/null; then
  # Works after a session-up that failed midway: recreate the kubeconfig, skip what was never created.
  aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION" --profile "$AWS_PROFILE_NAME" \
    --kubeconfig "$KUBECONFIG" >/dev/null
  chmod 600 "$KUBECONFIG"
  # Read volume IDs before the claims go away; the StorageClass retains PVs and their EBS.
  volumes=$(kubectl get pv -o jsonpath='{range .items[?(@.spec.storageClassName=="mosemo-kafka-gp3")]}{.metadata.name}{" "}{.spec.csi.volumeHandle}{"\n"}{end}')
  if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    kubectl -n "$NAMESPACE" delete statefulset mosemo-kafka --ignore-not-found --wait=true
    kubectl -n "$NAMESPACE" delete pvc data-mosemo-kafka-0 --ignore-not-found --wait=true
  fi
  pvs=$(awk '{print $1}' <<<"$volumes")
  volume_ids=$(awk '{print $2}' <<<"$volumes")
  for volume_id in $volume_ids; do
    [[ $volume_id == vol-* ]] || fail "a Kafka PV has no EBS volume ID: $volume_id"
  done
else
  echo "no cluster in the session state"
  pvs=""
  volume_ids=""
fi
# Also catch volumes whose PV was removed by an earlier, interrupted run.
tagged=$(ec2 describe-volumes --filters "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=$NAMESPACE" \
  "Name=tag:kubernetes.io/created-for/pvc/name,Values=data-mosemo-kafka-0" --query 'Volumes[].VolumeId' --output text)
for volume_id in $(tr -s ' \t' '\n' <<<"$volume_ids $tagged" | sort -u); do
  echo "deleting EBS $volume_id"
  ec2 wait volume-available --volume-ids "$volume_id" || fail "EBS $volume_id did not detach"
  ec2 delete-volume --volume-id "$volume_id"
  ec2 wait volume-deleted --volume-ids "$volume_id" || fail "EBS $volume_id was not deleted"
done
for pv in $pvs; do
  kubectl delete pv "$pv" --wait=true
done
leftover=$(ec2 describe-volumes --filters "Name=tag:kubernetes.io/created-for/pvc/namespace,Values=$NAMESPACE" \
  --query 'Volumes[].VolumeId' --output text)
[[ -z $leftover ]] || fail "EBS volumes created for namespace $NAMESPACE remain: $leftover"
echo "no EBS volumes remain for namespace $NAMESPACE"

step "RDS deletion protection and final snapshot"
if tf state list | grep -x 'aws_db_instance.main' >/dev/null; then
  # The provider reads skip_final_snapshot from state at destroy, so it is applied first.
  # Refuse the apply unless it only updates the database, plus on a rerun the ALB protection this script
  # lifted earlier (e.g. never finish a half-created session here).
  snapshot_plan="$SESSION_DIR/skip-final-snapshot.tfplan"
  tf plan -input=false -var skip_final_snapshot=true -out="$snapshot_plan" >/dev/null
  violations=$(tf show -json "$snapshot_plan" | snapshot_plan_violations)
  [[ -z $violations ]] \
    || fail "skip_final_snapshot plan changes more than allowed: $(tr '\n' ' ' <<<"$violations"). If session-up's apply failed midway, rerun session-up.sh until its apply succeeds, then rerun this script"
  tf apply -input=false "$snapshot_plan" >/dev/null
  rm -f "$snapshot_plan"
  # After that apply, so the apply cannot restore protection from configuration.
  rds modify-db-instance --db-instance-identifier "$CLUSTER_NAME" --no-deletion-protection --apply-immediately >/dev/null
  rds wait db-instance-available --db-instance-identifier "$CLUSTER_NAME"
  [[ $(rds describe-db-instances --db-instance-identifier "$CLUSTER_NAME" \
    --query 'DBInstances[0].DeletionProtection' --output text) == False ]] || fail "RDS deletion protection is still on"
else
  echo "no RDS instance in the session state"
fi

step "ALB deletion protection"
if tf state list | grep -x 'aws_lb.api' >/dev/null; then
  # After the snapshot apply, so that apply cannot restore protection from configuration.
  lb_arn=$(elbv2 describe-load-balancers --names "$CLUSTER_NAME" --query 'LoadBalancers[0].LoadBalancerArn' --output text)
  [[ $lb_arn == arn:aws:elasticloadbalancing:* ]] || fail "cannot find load balancer $CLUSTER_NAME: $lb_arn"
  elbv2 modify-load-balancer-attributes --load-balancer-arn "$lb_arn" \
    --attributes Key=deletion_protection.enabled,Value=false >/dev/null
  [[ $(elbv2 describe-load-balancer-attributes --load-balancer-arn "$lb_arn" \
    --query "Attributes[?Key=='deletion_protection.enabled'].Value" --output text) == false ]] \
    || fail "ALB deletion protection is still on"
else
  echo "no ALB in the session state"
fi

step "Destroy session stack (review the plan before answering yes)"
tf destroy -input=true -var skip_final_snapshot=true \
  || fail "destroy did not finish; RDS and ALB deletion protection may be OFF. Re-run session-down.sh, or run terraform apply to restore protection"
[[ -z $(tf state list) ]] || fail "session state still lists resources after destroy"

step "Clean session files"
rm -rf "$KUBECONFIG" "$RENDER_DIR" "$SESSION_DIR/rds-ca.pem"
echo "Session stack destroyed; foundation stack untouched."

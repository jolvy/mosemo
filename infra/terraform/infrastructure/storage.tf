resource "aws_eks_addon" "pod_identity" {
  cluster_name  = aws_eks_cluster.main.name
  addon_name    = "eks-pod-identity-agent"
  addon_version = "v1.3.10-eksbuild.3"

  depends_on = [aws_eks_node_group.main]
}

resource "aws_eks_pod_identity_association" "ebs_csi" {
  cluster_name    = aws_eks_cluster.main.name
  namespace       = "kube-system"
  service_account = "ebs-csi-controller-sa"
  # ../admin creates this role before an eventual infrastructure apply.
  # Use its known ARN so plan does not require the unapplied role to exist.
  role_arn = "arn:aws:iam::${var.aws_account_id}:role/mosemo-lab-ebs-csi"

  depends_on = [aws_eks_addon.pod_identity]
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name  = aws_eks_cluster.main.name
  addon_name    = "aws-ebs-csi-driver"
  addon_version = "v1.67.1-eksbuild.1"
  configuration_values = jsonencode({
    controller = {
      replicaCount        = 2
      podDisruptionBudget = { enabled = true }
    }
    # The node plugin must run on the tainted Kafka node to attach its volume.
    node     = { tolerateAllTaints = true }
    sidecars = { snapshotter = { forceEnable = false } }
  })

  depends_on = [aws_eks_pod_identity_association.ebs_csi]
}

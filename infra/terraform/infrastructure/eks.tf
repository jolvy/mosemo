# Provisioned separately by the administrator in ../admin.
data "aws_iam_role" "cluster" {
  name = "${local.name}-eks-cluster"
}

resource "aws_eks_cluster" "main" {
  name                          = local.name
  role_arn                      = data.aws_iam_role.cluster.arn
  version                       = "1.36"
  bootstrap_self_managed_addons = true

  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = true
  }

  vpc_config {
    subnet_ids              = [for subnet in aws_subnet.public : subnet.id]
    endpoint_public_access  = true
    endpoint_private_access = true
  }

}

data "aws_iam_role" "node" {
  name = "${local.name}-eks-node"
}

# Prefix delegation must be configured before any node launches: managed node
# groups compute max-pods from the CNI configuration when a node is created.
resource "aws_eks_addon" "vpc_cni" {
  cluster_name                = aws_eks_cluster.main.name
  addon_name                  = "vpc-cni"
  addon_version               = "v1.22.4-eksbuild.14"
  resolve_conflicts_on_create = "OVERWRITE"
  configuration_values = jsonencode({
    env = { ENABLE_PREFIX_DELEGATION = "true" }
    # Kubernetes NetworkPolicy enforcement (requires VPC CNI v1.21.0 or later).
    enableNetworkPolicy = "true"
  })
}

locals {
  # The measurement profile uses a non-burst family so a later same-spec re-measurement (after step 7)
  # does not depend on CPU credits; all of its nodes run in 2a.
  node_instance_type = var.measurement ? "m7i.large" : "t3.small"
  burstable_nodes    = startswith(local.node_instance_type, "t3.")
}

# No AMI: EKS keeps merging its bootstrap user data and calculating max-pods.
# The instance type lives here, not on the node groups (EKS rejects both): the AWS provider sends
# credit_specification only when the template itself names a burstable instance type.
resource "aws_launch_template" "node" {
  name                   = "${local.name}-node"
  instance_type          = local.node_instance_type
  description            = "Mosemo EKS managed nodes: ${local.burstable_nodes ? "standard T3 credits, " : ""}IMDSv2 hop limit 1, encrypted 20 GiB gp3 root volume, detailed monitoring."
  update_default_version = true

  # T3 only: never bill surplus credits; a throttled node is preferred over spending account credits.
  dynamic "credit_specification" {
    for_each = local.burstable_nodes ? [1] : []

    content {
      cpu_credits = "standard"
    }
  }

  # IMDSv2 only, unreachable from Pod network namespaces; host-network agents keep access.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = 20
      volume_type           = "gp3"
      delete_on_termination = true
      # No kms_key_id: the AWS managed key aws/ebs.
      encrypted = true
    }
  }

  # One-minute EC2 metrics for alarms and measurement windows; credit metrics stay at five minutes.
  monitoring {
    enabled = true
  }
}

locals {
  base_node_groups = {
    # Kafka's EBS volume is bound to 2a, so its node never leaves that AZ.
    kafka = {
      subnet_keys = ["a"]
      min_size    = 1
      max_size    = 1
      taints      = [{ key = "dedicated", value = "kafka", effect = "NO_SCHEDULE" }]
    }
    # Shared by API, consumer, migration, and system add-ons; the only ALB target.
    # Measurement: four fixed nodes in 2a; normal sessions: one to two nodes across 2a and 2b.
    app = {
      subnet_keys = var.measurement ? ["a"] : ["a", "b"]
      min_size    = var.measurement ? 4 : 1
      max_size    = var.measurement ? 4 : 2
      taints      = []
    }
  }
  # Measurement profile only: the load generator never shares a node with the API it measures.
  loadgen_node_group = {
    loadgen = {
      subnet_keys = ["a"]
      min_size    = 1
      max_size    = 1
      taints      = [{ key = "dedicated", value = "loadgen", effect = "NO_SCHEDULE" }]
    }
  }
  node_groups = merge(local.base_node_groups, { for key, group in local.loadgen_node_group : key => group if var.measurement })
}

resource "aws_eks_node_group" "main" {
  for_each = local.node_groups

  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name}-${each.key}"
  node_role_arn   = data.aws_iam_role.node.arn
  subnet_ids      = [for key in each.value.subnet_keys : aws_subnet.public[key].id]
  version         = aws_eks_cluster.main.version
  ami_type        = "AL2023_x86_64_STANDARD"
  capacity_type   = "ON_DEMAND"
  labels          = { pool = each.key }

  launch_template {
    id      = aws_launch_template.node.id
    version = aws_launch_template.node.latest_version
  }

  scaling_config {
    desired_size = each.value.min_size
    min_size     = each.value.min_size
    max_size     = each.value.max_size
  }

  dynamic "taint" {
    for_each = each.value.taints

    content {
      key    = taint.value.key
      value  = taint.value.value
      effect = taint.value.effect
    }
  }

  depends_on = [
    aws_eks_addon.vpc_cni,
    aws_route.internet,
    aws_route_table_association.public,
  ]
}

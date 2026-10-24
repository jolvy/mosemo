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

resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name}-public"
  node_role_arn   = data.aws_iam_role.node.arn
  # Replacement nodes must remain in the retained EBS volume's AZ.
  subnet_ids     = [aws_subnet.public["a"].id]
  version        = aws_eks_cluster.main.version
  ami_type       = "AL2023_x86_64_STANDARD"
  capacity_type  = "ON_DEMAND"
  instance_types = ["t3.small"]
  disk_size      = 20

  scaling_config {
    desired_size = 1
    min_size     = 1
    max_size     = 1
  }

  depends_on = [
    aws_route.internet,
    aws_route_table_association.public,
  ]
}

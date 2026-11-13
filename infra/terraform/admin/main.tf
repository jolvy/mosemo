terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  profile             = "mosemo"
  region              = "ap-southeast-2"
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "mosemo"
      Environment = "lab"
      ManagedBy   = "terraform"
    }
  }
}

locals {
  service_roles = {
    cluster = {
      name      = "mosemo-lab-eks-cluster"
      principal = "eks.amazonaws.com"
    }
    node = {
      name      = "mosemo-lab-eks-node"
      principal = "ec2.amazonaws.com"
    }
  }
  service_policies = {
    cluster = { role = "cluster", policy = "AmazonEKSClusterPolicy" }
    worker  = { role = "node", policy = "AmazonEKSWorkerNodePolicy" }
    pull    = { role = "node", policy = "AmazonEC2ContainerRegistryPullOnly" }
    cni     = { role = "node", policy = "AmazonEKS_CNI_Policy" }
  }
}

# These runtime roles are owned by the administrator bootstrap, not by the
# infrastructure executor. The latter can neither alter their trust nor grant
# additional policies to itself through them.
resource "aws_iam_role" "service" {
  for_each = local.service_roles

  name = each.value.name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = each.value.principal }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "service" {
  for_each = local.service_policies

  role       = aws_iam_role.service[each.value.role].name
  policy_arn = "arn:aws:iam::aws:policy/${each.value.policy}"
}

resource "aws_iam_role" "infrastructure" {
  name                 = "MosemoInfrastructureRole"
  description          = "Terraform management of Mosemo lab infrastructure in Sydney."
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = "arn:aws:iam::${var.aws_account_id}:role/managed/AccountFullAccessRole"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_policy" "infrastructure" {
  for_each = toset(["network", "services", "identity", "state", "addons", "https", "nodes", "volumes", "foundation"])

  name        = "MosemoInfrastructure-${each.key}"
  description = "Mosemo Terraform executor: ${each.key}."
  policy      = templatefile("${path.module}/policies/${each.key}.json", { account_id = var.aws_account_id })
}

resource "aws_iam_role_policy_attachment" "infrastructure" {
  for_each = aws_iam_policy.infrastructure

  role       = aws_iam_role.infrastructure.name
  policy_arn = each.value.arn
}

output "infrastructure_role_arn" {
  value = aws_iam_role.infrastructure.arn
}

output "service_role_arns" {
  value = { for key, role in aws_iam_role.service : key => role.arn }
}

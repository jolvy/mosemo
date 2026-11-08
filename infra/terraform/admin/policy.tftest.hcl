mock_provider "aws" {}

variables {
  aws_account_id = "123456789012"
}

run "bootstrap_permission_contract" {
  command = plan

  assert {
    condition = (
      jsondecode(aws_iam_role.infrastructure.assume_role_policy).Statement[0].Principal.AWS ==
      "arn:aws:iam::123456789012:role/managed/AccountFullAccessRole"
    )
    error_message = "Only the current administrator role may assume the executor role."
  }

  assert {
    condition     = aws_iam_role.infrastructure.max_session_duration == 3600
    error_message = "Role chaining must use a session of at most one hour."
  }

  assert {
    condition = (
      length(aws_iam_role.service) == 2 &&
      length(aws_iam_role_policy_attachment.service) == 4 &&
      length(aws_iam_policy.infrastructure) == 8 &&
      length(aws_iam_role_policy_attachment.infrastructure) == 8
    )
    error_message = "Keep existing roles and include only the dedicated storage role, addon, API certificate, node launch template, and Kafka volume cleanup permissions."
  }

  assert {
    condition = alltrue([
      for name in ["network", "services", "identity", "state", "addons", "https", "nodes", "volumes"] :
      length(jsonencode(jsondecode(aws_iam_policy.infrastructure[name].policy))) <= 6144
    ])
    error_message = "Each managed policy must fit the IAM 6144-character limit."
  }

  assert {
    condition = alltrue(flatten([
      for name in ["network", "services", "identity", "state", "addons", "https", "nodes", "volumes"] : [
        for statement in jsondecode(aws_iam_policy.infrastructure[name].policy).Statement : [
          for action in statement.Action :
          !strcontains(action, "*") && (
            !startswith(action, "iam:") || contains([
              "iam:GetRole", "iam:ListRoleTags", "iam:ListAttachedRolePolicies", "iam:PassRole", "iam:CreateServiceLinkedRole"
            ], action)
          )
        ]
      ]
    ]))
    error_message = "Executor permissions must not gain wildcard actions or general IAM mutation."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.infrastructure["identity"].policy).Statement :
      statement.Resource == [
        "arn:aws:iam::123456789012:role/mosemo-lab-eks-cluster",
        "arn:aws:iam::123456789012:role/mosemo-lab-eks-node"
      ]
      if contains(statement.Action, "iam:ListAttachedRolePolicies")
    ])
    error_message = "Policy attachment reads must be limited to the two preprovisioned service roles."
  }

  assert {
    condition = alltrue(flatten([
      for name in ["network", "services", "addons", "https", "nodes", "volumes"] : [
        for statement in jsondecode(aws_iam_policy.infrastructure[name].policy).Statement : [
          for action in statement.Action : startswith(action, "kms:") || try(
            statement.Condition.StringEquals["aws:RequestedRegion"] == "ap-southeast-2", false
          )
        ]
      ]
    ]))
    error_message = "Every regional operation must be constrained to Sydney."
  }

  assert {
    condition = (
      aws_iam_role.ebs_csi.name == "mosemo-lab-ebs-csi" &&
      aws_iam_role_policy_attachment.ebs_csi.policy_arn == "arn:aws:iam::aws:policy/AmazonEBSCSIDriverPolicyV2" &&
      jsondecode(aws_iam_role.ebs_csi.assume_role_policy).Statement[0].Condition.StringEquals["aws:RequestTag/eks-cluster-arn"] == "arn:aws:eks:ap-southeast-2:123456789012:cluster/mosemo-lab" &&
      jsondecode(aws_iam_role.ebs_csi.assume_role_policy).Statement[0].Condition.StringEquals["aws:RequestTag/kubernetes-service-account"] == "ebs-csi-controller-sa"
    )
    error_message = "EBS permissions must use the dedicated cluster-scoped controller Pod Identity role."
  }

  assert {
    condition = alltrue(flatten([
      for statement in jsondecode(aws_iam_policy.infrastructure["state"].policy).Statement : [
        for resource in statement.Resource :
        startswith(resource, "arn:aws:s3:::mosemo-tfstate-123456789012-ap-southeast-2") &&
        !strcontains(resource, "*")
      ]
    ]))
    error_message = "State access must not expand to wildcard buckets or objects."
  }
}


run "api_certificate_permission_contract" {
  command = plan

  assert {
    condition = (
      jsondecode(aws_iam_policy.infrastructure["https"].policy).Statement[0].Condition["ForAllValues:StringEquals"]["acm:DomainNames"] == ["mosemo-api.danuh.com"] &&
      jsondecode(aws_iam_policy.infrastructure["https"].policy).Statement[0].Condition.Null["acm:DomainNames"] == "false" &&
      jsondecode(aws_iam_policy.infrastructure["https"].policy).Statement[0].Condition.StringEquals["acm:Export"] == "DISABLED" &&
      jsondecode(aws_iam_policy.infrastructure["https"].policy).Statement[0].Condition.StringEquals["acm:ValidationMethod"] == "DNS" &&
      alltrue([
        for statement in jsondecode(aws_iam_policy.infrastructure["https"].policy).Statement :
        try(statement.Condition.StringEquals["aws:ResourceTag/Name"] == "mosemo-api.danuh.com", false)
        if contains(statement.Action, "acm:AddTagsToCertificate")
      ])
    )
    error_message = "Certificate requests must be DNS validated, non-exportable, and restricted to the chosen API hostname with a nonempty domain list."
  }
}

run "image_push_permission_contract" {
  command = plan

  assert {
    condition = length([
      for statement in jsondecode(aws_iam_policy.infrastructure["services"].policy).Statement : statement
      if statement.Action == ["ecr:GetAuthorizationToken"] && statement.Resource == ["*"] && statement.Effect == "Allow"
    ]) == 1
    error_message = "Registry authentication permission must exist exactly once."
  }

  assert {
    condition = alltrue([
      for action in ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages"] :
      length([
        for statement in jsondecode(aws_iam_policy.infrastructure["services"].policy).Statement : statement
        if contains(statement.Action, action) && statement.Resource == ["arn:aws:ecr:ap-southeast-2:123456789012:repository/mosemo-lab-server"]
      ]) == 1
    ])
    error_message = "Image push and read actions must target only the named application repository."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.infrastructure["services"].policy).Statement :
      statement.Action == ["ecr:GetAuthorizationToken"]
      if anytrue([for action in statement.Action : startswith(action, "ecr:")]) && statement.Resource == ["*"]
    ])
    error_message = "Only ECR registry authentication may use a wildcard resource."
  }
}

run "pod_identity_role_lookup_contract" {
  command = plan

  assert {
    condition = length([
      for statement in jsondecode(aws_iam_policy.infrastructure["identity"].policy).Statement : statement
      if statement.Sid == "ReadEbsCsiPodIdentityRole" && statement.Action == ["iam:GetRole"] && statement.Resource == ["arn:aws:iam::123456789012:role/mosemo-lab-ebs-csi"] && statement.Effect == "Allow"
    ]) == 1
    error_message = "Pod Identity creation requires lookup of only the dedicated CSI role."
  }
}

run "node_group_permission_contract" {
  command = plan

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.infrastructure["services"].policy).Statement :
      contains(statement.Resource, "arn:aws:eks:ap-southeast-2:123456789012:nodegroup/mosemo-lab/mosemo-lab-kafka/*") &&
      contains(statement.Resource, "arn:aws:eks:ap-southeast-2:123456789012:nodegroup/mosemo-lab/mosemo-lab-app/*") &&
      !contains(statement.Resource, "arn:aws:eks:ap-southeast-2:123456789012:nodegroup/mosemo-lab/mosemo-lab-public/*")
      if contains(statement.Action, "eks:CreateNodegroup") || contains(statement.Action, "eks:DescribeNodegroup")
    ])
    error_message = "Node group lifecycle must target exactly the kafka and app groups."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.infrastructure["services"].policy).Statement :
      statement.Resource == ["arn:aws:autoscaling:ap-southeast-2:123456789012:autoScalingGroup:*:autoScalingGroupName/eks-mosemo-lab-app-*"]
      if contains(statement.Action, "autoscaling:AttachLoadBalancerTargetGroups")
    ])
    error_message = "Only the app node group's ASG may be attached to the ALB target group."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.infrastructure["addons"].policy).Statement :
      contains(statement.Resource, "arn:aws:eks:ap-southeast-2:123456789012:addon/mosemo-lab/vpc-cni/*")
      if contains(statement.Action, "eks:UpdateAddon")
    ])
    error_message = "The executor must manage the vpc-cni add-on for prefix delegation."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.infrastructure["nodes"].policy).Statement :
      statement.Resource == ["arn:aws:ec2:ap-southeast-2:123456789012:launch-template/*"] && (
        try(statement.Condition.StringEquals["aws:RequestTag/Project"] == "mosemo", false) ||
        try(statement.Condition.StringEquals["aws:ResourceTag/Project"] == "mosemo", false) ||
        try(statement.Condition.StringEquals["ec2:CreateAction"] == "CreateLaunchTemplate", false)
      )
    ])
    error_message = "Launch template permissions must be limited to project-tagged templates or their creation-time tags."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.infrastructure["nodes"].policy).Statement : alltrue([
        for action in statement.Action : !contains(["ec2:RunInstances", "ec2:CreateTags"], action) || action == "ec2:CreateTags" && try(statement.Condition.StringEquals["ec2:CreateAction"] == "CreateLaunchTemplate", false)
      ])
    ])
    error_message = "Launch template permissions must not grant direct instance launches or general tagging."
  }
  assert {
    condition = alltrue([
      for action in ["eks:CreateNodegroup", "eks:DescribeNodegroup", "autoscaling:AttachLoadBalancerTargetGroups"] :
      length([for statement in jsondecode(aws_iam_policy.infrastructure["services"].policy).Statement : statement if contains(statement.Action, action)]) > 0
      ]) && length([
      for statement in jsondecode(aws_iam_policy.infrastructure["addons"].policy).Statement : statement if contains(statement.Action, "eks:UpdateAddon")
      ]) > 0 && alltrue([
      for action in ["ec2:CreateLaunchTemplate", "ec2:CreateLaunchTemplateVersion", "ec2:DeleteLaunchTemplate"] :
      length([for statement in jsondecode(aws_iam_policy.infrastructure["nodes"].policy).Statement : statement if contains(statement.Action, action)]) > 0
    ])
    error_message = "Node group, ALB attachment, add-on, and launch template permissions must exist, not just be scoped."
  }
}

run "foundation_state_permission_contract" {
  command = plan

  assert {
    condition = alltrue([
      for key in ["mosemo/lab/terraform.tfstate", "mosemo/foundation/terraform.tfstate"] :
      anytrue([
        for statement in jsondecode(aws_iam_policy.infrastructure["state"].policy).Statement :
        contains(statement.Resource, "arn:aws:s3:::mosemo-tfstate-123456789012-ap-southeast-2/${key}") &&
        contains(statement.Action, "s3:PutObject")
      ]) &&
      anytrue([
        for statement in jsondecode(aws_iam_policy.infrastructure["state"].policy).Statement :
        contains(statement.Resource, "arn:aws:s3:::mosemo-tfstate-123456789012-ap-southeast-2/${key}.tflock") &&
        contains(statement.Action, "s3:DeleteObject")
      ])
    ])
    error_message = "The executor must own the session and foundation state keys and their locks."
  }

  assert {
    condition = alltrue(flatten([
      for statement in jsondecode(aws_iam_policy.infrastructure["state"].policy).Statement : [
        for resource in statement.Resource : !strcontains(resource, "mosemo/iam/")
      ]
    ]))
    error_message = "The executor must never reach the administrator IAM state."
  }
}

run "session_cleanup_permission_contract" {
  command = plan

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.infrastructure["volumes"].policy).Statement :
      statement.Condition.StringEquals["aws:ResourceTag/kubernetes.io/created-for/pvc/namespace"] == "mosemo" &&
      statement.Condition.StringEquals["aws:ResourceTag/kubernetes.io/created-for/pvc/name"] == "data-mosemo-kafka-0" &&
      statement.Condition.StringEquals["aws:ResourceTag/ebs.csi.aws.com/cluster"] == "true"
      if contains(statement.Action, "ec2:DeleteVolume")
    ])
    error_message = "Volume deletion must be limited to the CSI-provisioned Kafka PVC volume."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.infrastructure["services"].policy).Statement :
      contains(statement.Resource, "arn:aws:rds:ap-southeast-2:123456789012:snapshot:mosemo-lab-final") &&
      !anytrue([for resource in statement.Resource : endswith(resource, "snapshot:*")])
      if contains(statement.Action, "rds:CreateDBSnapshot")
    ])
    error_message = "Only the named final snapshot of the session database may be created."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_iam_policy.infrastructure["volumes"].policy).Statement : statement if contains(statement.Action, "ec2:DeleteVolume")
      ]) == 1 && length([
      for statement in jsondecode(aws_iam_policy.infrastructure["services"].policy).Statement : statement if contains(statement.Action, "rds:CreateDBSnapshot")
    ]) == 1
    error_message = "Kafka volume deletion and the final snapshot permission must each exist exactly once."
  }
}

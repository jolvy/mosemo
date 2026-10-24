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
      length(aws_iam_policy.infrastructure) == 6 &&
      length(aws_iam_role_policy_attachment.infrastructure) == 6
    )
    error_message = "Keep existing roles and include only the dedicated storage role, addon permissions, and API certificate permissions."
  }

  assert {
    condition = alltrue([
      for name in ["network", "services", "identity", "state", "addons", "https"] :
      length(jsonencode(jsondecode(aws_iam_policy.infrastructure[name].policy))) <= 6144
    ])
    error_message = "Each managed policy must fit the IAM 6144-character limit."
  }

  assert {
    condition = alltrue(flatten([
      for name in ["network", "services", "identity", "state", "addons", "https"] : [
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
      for name in ["network", "services", "addons", "https"] : [
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

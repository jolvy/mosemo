mock_provider "aws" {
  mock_data "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" }
  }
  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:ap-southeast-2:123456789012:loadbalancer/app/mosemo-lab/mock"
      dns_name = "mosemo-lab-123.ap-southeast-2.elb.amazonaws.com"
    }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-southeast-2:123456789012:targetgroup/mosemo-lab-api/mock" }
  }
  mock_resource "aws_launch_template" {
    defaults = { id = "lt-0123456789abcdef0", latest_version = 1 }
  }
  mock_resource "aws_eks_node_group" {
    defaults = {
      resources = [{
        autoscaling_groups              = [{ name = "eks-mosemo-lab-kafka-mock" }]
        remote_access_security_group_id = ""
      }]
    }
  }
}

mock_provider "cloudflare" {}

override_data {
  target = data.terraform_remote_state.foundation
  values = {
    outputs = {
      ecr_repository_url  = "123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/mosemo-lab-server"
      api_certificate_arn = "arn:aws:acm:ap-southeast-2:123456789012:certificate/00000000-0000-0000-0000-000000000000"
    }
  }
}

override_resource {
  target = aws_eks_node_group.main["app"]
  values = {
    resources = [{
      autoscaling_groups              = [{ name = "eks-mosemo-lab-app-mock" }]
      remote_access_security_group_id = ""
    }]
  }
}

variables {
  aws_account_id     = "123456789012"
  db_password        = "FakePlanOnlyPassword123"
  cloudflare_zone_id = "0123456789abcdef0123456789abcdef"
}

run "session_stack_contract" {
  command = apply

  assert {
    condition = (
      aws_lb_listener.https.port == 443 &&
      aws_lb_listener.https.protocol == "HTTPS" &&
      aws_lb_listener.https.ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06" &&
      aws_lb_listener.https.certificate_arn == "arn:aws:acm:ap-southeast-2:123456789012:certificate/00000000-0000-0000-0000-000000000000" &&
      aws_lb_listener.https.default_action[0].type == "forward" &&
      aws_vpc_security_group_ingress_rule.https.from_port == 443 &&
      output.api_url == "https://mosemo-api.danuh.com" &&
      output.ecr_repository_url == "123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/mosemo-lab-server"
    )
    error_message = "HTTPS must be served from the first apply with the foundation certificate, TLS 1.2/1.3, and the canonical API URL."
  }

  assert {
    condition = (
      aws_lb_listener.http.default_action[0].type == "redirect" &&
      aws_lb_listener.http.default_action[0].redirect[0].host == "mosemo-api.danuh.com" &&
      aws_lb_listener.http.default_action[0].redirect[0].protocol == "HTTPS"
    )
    error_message = "HTTP must redirect to the canonical HTTPS hostname, never expose an insecure API."
  }

  assert {
    condition = (
      cloudflare_dns_record.api.name == "mosemo-api.danuh.com" &&
      cloudflare_dns_record.api.type == "CNAME" &&
      cloudflare_dns_record.api.content == "mosemo-lab-123.ap-southeast-2.elb.amazonaws.com" &&
      cloudflare_dns_record.api.proxied == false &&
      cloudflare_dns_record.api.ttl == 300
    )
    error_message = "The API hostname must be a DNS-only CNAME to this session's ALB with TTL 300."
  }

  assert {
    condition = (
      aws_eks_node_group.main["kafka"].node_group_name == "mosemo-lab-kafka" &&
      aws_eks_node_group.main["kafka"].scaling_config[0].min_size == 1 &&
      aws_eks_node_group.main["kafka"].scaling_config[0].desired_size == 1 &&
      aws_eks_node_group.main["kafka"].scaling_config[0].max_size == 1 &&
      length(aws_eks_node_group.main["kafka"].subnet_ids) == 1 &&
      contains(aws_eks_node_group.main["kafka"].subnet_ids, aws_subnet.public["a"].id) &&
      aws_eks_node_group.main["kafka"].labels["pool"] == "kafka" &&
      length(aws_eks_node_group.main["kafka"].taint) == 1 &&
      one(aws_eks_node_group.main["kafka"].taint).key == "dedicated" &&
      one(aws_eks_node_group.main["kafka"].taint).value == "kafka" &&
      one(aws_eks_node_group.main["kafka"].taint).effect == "NO_SCHEDULE"
    )
    error_message = "Kafka must run on one tainted t3.small node fixed to ap-southeast-2a."
  }

  assert {
    condition = (
      aws_eks_node_group.main["app"].node_group_name == "mosemo-lab-app" &&
      aws_eks_node_group.main["app"].scaling_config[0].min_size == 1 &&
      aws_eks_node_group.main["app"].scaling_config[0].desired_size == 1 &&
      aws_eks_node_group.main["app"].scaling_config[0].max_size == 2 &&
      toset(aws_eks_node_group.main["app"].subnet_ids) == toset([aws_subnet.public["a"].id, aws_subnet.public["b"].id]) &&
      aws_eks_node_group.main["app"].labels["pool"] == "app" &&
      length(aws_eks_node_group.main["app"].taint) == 0
    )
    error_message = "The shared app group must span 2a and 2b with one to two untainted nodes."
  }

  assert {
    condition = alltrue([
      for group in aws_eks_node_group.main :
      length(group.instance_types) == 1 && contains(group.instance_types, "t3.small") &&
      group.capacity_type == "ON_DEMAND" &&
      group.launch_template[0].id == aws_launch_template.node.id &&
      group.launch_template[0].version == tostring(aws_launch_template.node.latest_version)
    ])
    error_message = "Every node must be an on-demand t3.small launched from the shared template."
  }

  assert {
    condition = (
      aws_launch_template.node.credit_specification[0].cpu_credits == "standard" &&
      aws_launch_template.node.metadata_options[0].http_tokens == "required" &&
      aws_launch_template.node.metadata_options[0].http_put_response_hop_limit == 1 &&
      aws_launch_template.node.image_id == null &&
      aws_launch_template.node.instance_type == null &&
      aws_launch_template.node.user_data == null
    )
    error_message = "The launch template must set standard credits and IMDSv2 hop limit 1, leaving AMI, bootstrap, and max-pods to EKS."
  }

  assert {
    condition = (
      aws_eks_addon.vpc_cni.addon_name == "vpc-cni" &&
      jsondecode(aws_eks_addon.vpc_cni.configuration_values).env.ENABLE_PREFIX_DELEGATION == "true"
    )
    error_message = "The VPC CNI must enable prefix delegation."
  }

  assert {
    condition     = aws_autoscaling_attachment.api.autoscaling_group_name == "eks-mosemo-lab-app-mock"
    error_message = "Only the app node group's ASG may be registered with the ALB target group."
  }

  assert {
    condition     = jsondecode(aws_eks_addon.ebs_csi.configuration_values).node.tolerateAllTaints == true
    error_message = "The EBS CSI node plugin must run on the tainted Kafka node."
  }

  assert {
    condition = (
      aws_db_instance.main.backup_retention_period == 7 &&
      aws_db_instance.main.deletion_protection == true &&
      aws_db_instance.main.skip_final_snapshot == false &&
      aws_db_instance.main.final_snapshot_identifier == "mosemo-lab-final"
    )
    error_message = "RDS must keep production protection by default: 7-day backups, deletion protection, and a final snapshot."
  }
}

run "session_down_skips_final_snapshot_only_when_asked" {
  command = plan

  variables {
    skip_final_snapshot = true
  }

  assert {
    condition     = aws_db_instance.main.skip_final_snapshot == true && aws_db_instance.main.deletion_protection == true
    error_message = "The session-down variable may skip only the final snapshot; deletion protection stays in configuration."
  }
}

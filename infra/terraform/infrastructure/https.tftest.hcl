mock_provider "aws" {
  mock_data "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock-role" }
  }
  mock_resource "aws_acm_certificate" {
    defaults = { arn = "arn:aws:acm:ap-southeast-2:123456789012:certificate/00000000-0000-0000-0000-000000000000" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-southeast-2:123456789012:loadbalancer/app/mosemo-lab/mock" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-southeast-2:123456789012:targetgroup/mosemo-lab-api/mock" }
  }
}

variables {
  aws_account_id = "123456789012"
  db_password    = "FakePlanOnlyPassword123"
}

run "certificate_request_stage" {
  command = plan
  variables {
    enable_https_listener = false
  }

  assert {
    condition = (
      length(aws_acm_certificate_validation.api) == 0 &&
      length(aws_lb_listener.https) == 0 &&
      length(aws_vpc_security_group_ingress_rule.https) == 0 &&
      aws_acm_certificate.api.domain_name == "mosemo-api.danuh.com" &&
      aws_acm_certificate.api.options[0].export == "DISABLED"
    )
    error_message = "The initial stage must request only a non-exportable certificate without waiting for external DNS or opening 443."
  }

  assert {
    condition = (
      aws_lb_listener.http.default_action[0].type == "redirect" &&
      aws_lb_listener.http.default_action[0].redirect[0].host == "mosemo-api.danuh.com" &&
      aws_lb_listener.http.default_action[0].redirect[0].protocol == "HTTPS"
    )
    error_message = "HTTP must redirect to the canonical HTTPS hostname, never expose an insecure API during bootstrap."
  }
}

run "https_service_stage" {
  command = plan

  assert {
    condition = (
      length(aws_acm_certificate_validation.api) == 1 &&
      length(aws_lb_listener.https) == 1 &&
      aws_lb_listener.https[0].port == 443 &&
      aws_lb_listener.https[0].protocol == "HTTPS" &&
      aws_lb_listener.https[0].ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06" &&
      aws_lb_listener.https[0].default_action[0].type == "forward" &&
      aws_vpc_security_group_ingress_rule.https[0].from_port == 443 &&
      output.api_url == "https://mosemo-api.danuh.com"
    )
    error_message = "The default service stage must enable certificate validation, TLS 1.2/1.3, HTTPS ingress, and the canonical API URL."
  }

  assert {
    condition = (
      length(aws_eks_node_group.main.instance_types) == 1 &&
      contains(aws_eks_node_group.main.instance_types, "t3.small") &&
      aws_eks_node_group.main.scaling_config[0].desired_size == 1 &&
      aws_eks_node_group.main.scaling_config[0].max_size == 1
    )
    error_message = "HTTPS must preserve the single t3.small node boundary."
  }
}

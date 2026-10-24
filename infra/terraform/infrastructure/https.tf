locals {
  api_hostname = "mosemo-api.danuh.com"
}

resource "aws_acm_certificate" "api" {
  domain_name       = local.api_hostname
  validation_method = "DNS"
  key_algorithm     = "RSA_2048"

  options {
    certificate_transparency_logging_preference = "ENABLED"
    export                                      = "DISABLED"
  }

  tags = { Name = local.api_hostname }

  lifecycle {
    create_before_destroy = true
  }
}

# Cloudflare owns DNS. The initial stage requests the certificate without waiting
# for a CNAME that cannot be known before ACM returns it. No Route 53 resources.
resource "aws_acm_certificate_validation" "api" {
  count = var.enable_https_listener ? 1 : 0

  certificate_arn         = aws_acm_certificate.api.arn
  validation_record_fqdns = [for record in aws_acm_certificate.api.domain_validation_options : record.resource_record_name]

  timeouts {
    create = "10m"
  }
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  count = var.enable_https_listener ? 1 : 0

  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_lb_listener" "https" {
  count = var.enable_https_listener ? 1 : 0

  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.api[0].certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

locals {
  api_hostname = "mosemo-api.danuh.com"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = data.terraform_remote_state.foundation.outputs.api_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# Same lifetime as the ALB: created and destroyed with each session.
resource "cloudflare_dns_record" "api" {
  zone_id = var.cloudflare_zone_id
  name    = local.api_hostname
  type    = "CNAME"
  content = aws_lb.api.dns_name
  proxied = false
  ttl     = 300
  comment = "Mosemo API ALB; managed by Terraform (mosemo session stack)."
}

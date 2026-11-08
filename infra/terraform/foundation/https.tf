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

locals {
  # Single-name certificate: exactly one validation record.
  api_validation = one([
    for option in aws_acm_certificate.api.domain_validation_options : option
    if option.domain_name == local.api_hostname
  ])
}

# Cloudflare owns DNS; no Route 53 resources. Keep this record for renewals.
resource "cloudflare_dns_record" "acm_validation" {
  zone_id = var.cloudflare_zone_id
  name    = trimsuffix(local.api_validation.resource_record_name, ".")
  type    = local.api_validation.resource_record_type
  content = trimsuffix(local.api_validation.resource_record_value, ".")
  proxied = false
  ttl     = 300
  comment = "ACM DNS validation for ${local.api_hostname}; managed by Terraform (mosemo foundation)."
}

resource "aws_acm_certificate_validation" "api" {
  certificate_arn         = aws_acm_certificate.api.arn
  validation_record_fqdns = [cloudflare_dns_record.acm_validation.name]

  timeouts {
    create = "10m"
  }
}

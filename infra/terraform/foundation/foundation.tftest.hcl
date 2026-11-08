mock_provider "aws" {
  mock_resource "aws_acm_certificate" {
    defaults = {
      arn = "arn:aws:acm:ap-southeast-2:123456789012:certificate/00000000-0000-0000-0000-000000000000"
      domain_validation_options = [{
        domain_name           = "mosemo-api.danuh.com"
        resource_record_name  = "_abc.mosemo-api.danuh.com."
        resource_record_type  = "CNAME"
        resource_record_value = "_def.acm-validations.aws."
      }]
    }
  }
}

mock_provider "cloudflare" {}

variables {
  aws_account_id     = "123456789012"
  cloudflare_zone_id = "0123456789abcdef0123456789abcdef"
}

run "certificate_validated_through_cloudflare" {
  command = apply

  assert {
    condition = (
      aws_acm_certificate.api.domain_name == "mosemo-api.danuh.com" &&
      aws_acm_certificate.api.validation_method == "DNS" &&
      aws_acm_certificate.api.options[0].export == "DISABLED"
    )
    error_message = "The API certificate must stay a non-exportable DNS-validated certificate for the canonical hostname."
  }

  assert {
    condition = (
      cloudflare_dns_record.acm_validation.name == "_abc.mosemo-api.danuh.com" &&
      cloudflare_dns_record.acm_validation.content == "_def.acm-validations.aws" &&
      cloudflare_dns_record.acm_validation.type == "CNAME" &&
      cloudflare_dns_record.acm_validation.proxied == false &&
      cloudflare_dns_record.acm_validation.ttl == 300
    )
    error_message = "ACM validation must be a DNS-only CNAME with TTL 300 in the Cloudflare zone."
  }

  assert {
    condition     = aws_acm_certificate_validation.api.validation_record_fqdns == toset(["_abc.mosemo-api.danuh.com"])
    error_message = "Certificate validation must wait for the Cloudflare validation record."
  }

  assert {
    condition     = aws_ecr_repository.server.name == "mosemo-lab-server"
    error_message = "The application repository name is referenced by IAM and must not change."
  }
}

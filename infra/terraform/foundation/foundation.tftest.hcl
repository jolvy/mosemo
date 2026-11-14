mock_provider "aws" {
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mosemo-lab-alb-logs-123456789012" }
  }
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

  assert {
    condition = (
      aws_ecr_repository.server.image_tag_mutability == "IMMUTABLE" &&
      aws_ecr_repository.server.image_scanning_configuration[0].scan_on_push == true &&
      aws_ecr_repository.server.force_delete == false
    )
    error_message = "Released images must be immutable, scanned on push, and never deleted with the repository."
  }

  assert {
    condition = (
      aws_s3_bucket.alb_logs.bucket == "mosemo-lab-alb-logs-123456789012" &&
      aws_s3_bucket.alb_logs.force_destroy == false &&
      output.alb_access_log_bucket == "mosemo-lab-alb-logs-123456789012"
    )
    error_message = "The ALB log bucket must keep its account-scoped name and refuse deletion while it holds logs."
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.alb_logs.block_public_acls &&
      aws_s3_bucket_public_access_block.alb_logs.block_public_policy &&
      aws_s3_bucket_public_access_block.alb_logs.ignore_public_acls &&
      aws_s3_bucket_public_access_block.alb_logs.restrict_public_buckets &&
      one(aws_s3_bucket_ownership_controls.alb_logs.rule).object_ownership == "BucketOwnerEnforced"
    )
    error_message = "The ALB log bucket must block all public access and disable ACLs."
  }

  assert {
    condition = (
      one(one(aws_s3_bucket_server_side_encryption_configuration.alb_logs.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256" &&
      one(aws_s3_bucket_lifecycle_configuration.alb_logs.rule).status == "Enabled" &&
      one(one(aws_s3_bucket_lifecycle_configuration.alb_logs.rule).expiration).days == 90
    )
    error_message = "ALB logs must use SSE-S3 (the only supported option) and expire after 90 days."
  }

  assert {
    condition = (
      length(jsondecode(aws_s3_bucket_policy.alb_logs.policy).Statement) == 1 &&
      one(jsondecode(aws_s3_bucket_policy.alb_logs.policy).Statement).Principal == { Service = "logdelivery.elasticloadbalancing.amazonaws.com" } &&
      one(jsondecode(aws_s3_bucket_policy.alb_logs.policy).Statement).Action == "s3:PutObject" &&
      one(jsondecode(aws_s3_bucket_policy.alb_logs.policy).Statement).Resource == "arn:aws:s3:::mosemo-lab-alb-logs-123456789012/mosemo-lab/AWSLogs/123456789012/*" &&
      one(jsondecode(aws_s3_bucket_policy.alb_logs.policy).Statement).Condition.ArnLike["aws:SourceArn"] == "arn:aws:elasticloadbalancing:ap-southeast-2:123456789012:loadbalancer/*"
    )
    error_message = "Only the ELB log delivery service may write, only under this account's mosemo-lab prefix, only for Sydney load balancers of this account."
  }
}

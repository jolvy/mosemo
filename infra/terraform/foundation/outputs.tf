# The session stack (../infrastructure) reads only these outputs.
output "ecr_repository_url" {
  value = aws_ecr_repository.server.repository_url
}

output "api_certificate_arn" {
  description = "ARN of the issued certificate; available only after DNS validation succeeds."
  value       = aws_acm_certificate_validation.api.certificate_arn
}

output "alb_access_log_bucket" {
  description = "Bucket for the session ALB access logs (prefix mosemo-lab)."
  value       = aws_s3_bucket.alb_logs.bucket
}

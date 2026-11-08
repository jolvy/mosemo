# The session stack (../infrastructure) reads only these two outputs.
output "ecr_repository_url" {
  value = aws_ecr_repository.server.repository_url
}

output "api_certificate_arn" {
  description = "ARN of the issued certificate; available only after DNS validation succeeds."
  value       = aws_acm_certificate_validation.api.certificate_arn
}

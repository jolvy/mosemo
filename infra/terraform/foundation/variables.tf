variable "aws_account_id" {
  description = "Target AWS account ID. Supply through an uncommitted terraform.tfvars."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Use a 12-digit AWS account ID."
  }
}

variable "aws_profile" {
  description = "Local AWS CLI profile used for this stack."
  type        = string
  default     = "mosemo-infra"
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID of danuh.com. Supply through an uncommitted terraform.tfvars."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.cloudflare_zone_id))
    error_message = "Use the 32-character hexadecimal Cloudflare zone ID."
  }
}

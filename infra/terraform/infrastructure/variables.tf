variable "aws_account_id" {
  description = "Target AWS account ID. Supply through an uncommitted terraform.tfvars."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Use a 12-digit AWS account ID."
  }
}

variable "aws_profile" {
  description = "Local AWS CLI profile used for planning this lab."
  type        = string
  default     = "mosemo-infra"
}

variable "db_password" {
  description = "RDS master password. Supply again for any future apply; it is not saved in state or plan files."
  type        = string
  sensitive   = true
  ephemeral   = true

  validation {
    condition = (
      length(var.db_password) >= 8 && length(var.db_password) <= 128 &&
      can(regex("^[!-~]+$", var.db_password)) &&
      !can(regex("[/@\"]", var.db_password))
    )
    error_message = "Use 8-128 printable ASCII characters without spaces, /, @, or double quotes."
  }
}

variable "skip_final_snapshot" {
  description = "Keep false. session-down.sh applies true, then lifts deletion protection, then destroys the session."
  type        = bool
  default     = false
}

variable "measurement" {
  description = "Step 5 measurement profile: adds the loadgen node group. Set only through session-lib.sh (TF_VAR_measurement)."
  type        = bool
  default     = false
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID of danuh.com. Supply through an uncommitted terraform.tfvars."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.cloudflare_zone_id))
    error_message = "Use the 32-character hexadecimal Cloudflare zone ID."
  }
}

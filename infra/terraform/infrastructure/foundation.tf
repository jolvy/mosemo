# Long-lived ECR and ACM live in ../foundation and survive session destroys.
# Apply that stack first; this session stack reads only its outputs (ECR, certificate, ALB log bucket).
data "terraform_remote_state" "foundation" {
  backend = "s3"

  config = {
    bucket  = "mosemo-tfstate-${var.aws_account_id}-ap-southeast-2"
    key     = "mosemo/foundation/terraform.tfstate"
    region  = "ap-southeast-2"
    profile = var.aws_profile
  }
}

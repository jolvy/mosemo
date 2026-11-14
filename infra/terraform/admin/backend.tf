terraform {
  # Administrator-owned IAM state, separate from mosemo/lab/terraform.tfstate.
  # bucket and allowed_account_ids come from the uncommitted backend.hcl.
  backend "s3" {
    key          = "mosemo/iam/terraform.tfstate"
    region       = "ap-southeast-2"
    profile      = "mosemo"
    encrypt      = true
    use_lockfile = true
  }
}

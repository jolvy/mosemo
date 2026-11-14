terraform {
  # Bootstrapped separately from application resources; retain state history.
  # bucket comes from the uncommitted backend.hcl.
  backend "s3" {
    key          = "mosemo/lab/terraform.tfstate"
    region       = "ap-southeast-2"
    profile      = "mosemo-infra"
    encrypt      = true
    use_lockfile = true
  }
}

terraform {
  # Long-lived resources that survive session destroys of mosemo/lab.
  # bucket comes from the uncommitted backend.hcl.
  backend "s3" {
    key          = "mosemo/foundation/terraform.tfstate"
    region       = "ap-southeast-2"
    profile      = "mosemo-infra"
    encrypt      = true
    use_lockfile = true
  }
}

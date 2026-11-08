provider "aws" {
  profile             = var.aws_profile
  region              = "ap-southeast-2"
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "mosemo"
      Environment = "lab"
      ManagedBy   = "terraform"
    }
  }
}

# Authenticates only through CLOUDFLARE_API_TOKEN (danuh.com Zone:DNS:Edit).
provider "cloudflare" {}

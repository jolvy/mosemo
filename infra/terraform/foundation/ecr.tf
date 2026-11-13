locals {
  name = "mosemo-lab"
}

resource "aws_ecr_repository" "server" {
  name = "${local.name}-server"

  # Commit-SHA tags are never rewritten; a pushed tag always names the same digest.
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # Released images outlive sessions; deleting the repository with images must fail.
  force_delete = false
}

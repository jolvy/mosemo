resource "aws_ecr_repository" "server" {
  name = "${local.name}-server"

  # Disposable lab: allow deleting the repository even after images are pushed.
  force_delete = true
}

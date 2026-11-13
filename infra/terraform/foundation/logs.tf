# ALB access logs outlive session destroys; the session ALB writes under the mosemo-lab/ prefix.
resource "aws_s3_bucket" "alb_logs" {
  bucket        = "${local.name}-alb-logs-${var.aws_account_id}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# ALB access logs support only SSE-S3, not SSE-KMS.
resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire-access-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = 90
    }
  }
}

locals {
  alb_log_prefix = "mosemo-lab"
}

# Log delivery service principal (current AWS recommendation over the legacy Regional ELB account),
# limited to this account's object path and to load balancers of this account in Sydney.
resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowAlbAccessLogDelivery"
      Effect    = "Allow"
      Principal = { Service = "logdelivery.elasticloadbalancing.amazonaws.com" }
      Action    = "s3:PutObject"
      Resource  = "${aws_s3_bucket.alb_logs.arn}/${local.alb_log_prefix}/AWSLogs/${var.aws_account_id}/*"
      Condition = {
        ArnLike = {
          "aws:SourceArn" = "arn:aws:elasticloadbalancing:ap-southeast-2:${var.aws_account_id}:loadbalancer/*"
        }
      }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.alb_logs]
}

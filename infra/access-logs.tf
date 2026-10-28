# Existing-network deployments retain their ALB access logs. The application
# owns the bucket and delivery settings rather than relying on unmanaged state.
resource "aws_s3_bucket" "alb_logs" {
  count  = var.existing_network == null ? 0 : 1
  bucket = var.existing_network.load_balancer_logs_bucket

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-alb-logs" })
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  count  = var.existing_network == null ? 0 : 1
  bucket = aws_s3_bucket.alb_logs[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  count  = var.existing_network == null ? 0 : 1
  bucket = aws_s3_bucket.alb_logs[0].id

  rule {
    id     = "expire-alb-access-logs"
    status = "Enabled"
    filter {}
    expiration { days = 30 }
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  count  = var.existing_network == null ? 0 : 1
  bucket = aws_s3_bucket.alb_logs[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowLoadBalancerLogDeliveryWrite"
      Effect    = "Allow"
      Principal = { Service = "logdelivery.elasticloadbalancing.amazonaws.com" }
      Action    = "s3:PutObject"
      Resource  = "${aws_s3_bucket.alb_logs[0].arn}/alb/${var.environment}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
    }]
  })
}

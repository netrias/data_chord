resource "aws_dynamodb_table" "harmonization_cache" {
  name         = "${local.name_prefix}-harmonization-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "cache_key"

  attribute {
    name = "cache_key"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-harmonization-cache"
  })
}

resource "aws_iam_role_policy" "application_task_harmonization_cache" {
  name = "${local.name_prefix}-harmonization-cache"
  role = aws_iam_role.application_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:DescribeTable",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
      ]
      Resource = aws_dynamodb_table.harmonization_cache.arn
    }]
  })
}

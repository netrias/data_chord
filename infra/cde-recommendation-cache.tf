resource "aws_dynamodb_table" "cde_recommendation_cache" {
  name         = "${local.name_prefix}-cde-recommendation-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "cache_key"

  attribute {
    name = "cache_key"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-cde-recommendation-cache"
  })
}

resource "aws_iam_role_policy" "application_task_cde_recommendation_cache" {
  name = "${local.name_prefix}-cde-recommendation-cache"
  role = aws_iam_role.application_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:BatchGetItem",
        "dynamodb:BatchWriteItem",
        "dynamodb:DescribeTable",
      ]
      Resource = aws_dynamodb_table.cde_recommendation_cache.arn
    }]
  })
}

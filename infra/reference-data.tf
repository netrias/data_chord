resource "aws_dynamodb_table" "reference_data" {
  name         = "${local.name_prefix}-reference-data"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = var.environment == "prod"

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-reference-data"
  })
}

resource "aws_iam_role_policy" "application_task_reference_data" {
  name = "${local.name_prefix}-reference-data-read"
  role = aws_iam_role.application_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:DescribeTable",
        "dynamodb:Query",
      ]
      Resource = aws_dynamodb_table.reference_data.arn
    }]
  })
}

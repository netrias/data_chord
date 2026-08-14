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

resource "aws_iam_role" "reference_data_importer" {
  count = var.enable_reference_data_importer ? 1 : 0

  name                 = "${local.name_prefix}-reference-data-importer"
  path                 = var.application_role_path
  permissions_boundary = var.application_role_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "reference_data_importer" {
  count = var.enable_reference_data_importer ? 1 : 0

  name = "${local.name_prefix}-reference-data-import"
  role = aws_iam_role.reference_data_importer[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:DescribeTable",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query",
      ]
      Resource = aws_dynamodb_table.reference_data.arn
    }]
  })
}

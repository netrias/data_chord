resource "aws_iam_role_policy" "application_task_reference_data" {
  name = "${local.name_prefix}-reference-data-read"
  role = aws_iam_role.application_task.id

  policy = module.data_plane.reference_policy_json
}

variable "reference_catalog" {
  description = "Keep the legacy catalog, prepare a parallel flat catalog, or switch to the flat catalog."
  type        = string
  default     = "legacy"

  validation {
    condition     = contains(["legacy", "prepare_flat", "flat"], var.reference_catalog)
    error_message = "reference_catalog must be legacy, prepare_flat, or flat."
  }
}

locals {
  active_reference_table = var.reference_catalog == "flat" ? aws_dynamodb_table.flat_reference_catalog[0].name : module.data_plane.reference_data_table_name
}

resource "aws_dynamodb_table" "flat_reference_catalog" {
  count = var.reference_catalog == "legacy" ? 0 : 1

  name                        = "${local.name_prefix}-reference-data-flat"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true

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

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-reference-data-flat" })
}

resource "aws_iam_role_policy" "application_task_flat_reference_catalog" {
  count = length(aws_dynamodb_table.flat_reference_catalog)

  name = "${local.name_prefix}-flat-reference-catalog-read"
  role = aws_iam_role.application_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:DescribeTable", "dynamodb:Query"]
      Resource = aws_dynamodb_table.flat_reference_catalog[count.index].arn
    }]
  })
}

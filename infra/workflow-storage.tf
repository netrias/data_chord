resource "aws_s3_bucket" "workflow" {
  bucket = "${local.name_prefix}-workflow-${data.aws_caller_identity.current.account_id}-${var.aws_region}"

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-workflow"
  })
}

resource "aws_s3_bucket_public_access_block" "workflow" {
  bucket = aws_s3_bucket.workflow.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "workflow" {
  bucket = aws_s3_bucket.workflow.id

  versioning_configuration {
    # Workflow writes use optimistic version checks, so keeping object versions
    # gives operators a recovery trail when a bad deploy corrupts artifacts.
    status = "Enabled"
  }
}

resource "aws_iam_role" "application_task" {
  name                 = "${local.name_prefix}-application-task"
  path                 = var.application_role_path
  permissions_boundary = var.application_role_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "application_task_workflow_storage" {
  name = "${local.name_prefix}-workflow-storage"
  role = aws_iam_role.application_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = "${aws_s3_bucket.workflow.arn}/${local.deployment_prefix}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.workflow.arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["${local.deployment_prefix}/*"]
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "application_task_bedrock_mantle" {
  count = var.harmonizer == "agentic" ? 1 : 0

  name = "${local.name_prefix}-bedrock-mantle"
  role = aws_iam_role.application_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:CallWithBearerToken",
        "bedrock-mantle:CallWithBearerToken",
      ]
      Resource = "*"
    }]
  })
}

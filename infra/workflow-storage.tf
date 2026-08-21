removed {
  from = aws_s3_bucket_versioning.workflow

  lifecycle {
    # Existing deployments can keep versioning. New deployments do not enable it.
    destroy = false
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

  policy = module.data_plane.workflow_policy_json
}

resource "aws_iam_role_policy" "application_task_bedrock_mantle" {
  name = "${local.name_prefix}-bedrock-mantle"
  role = aws_iam_role.application_task.id

  policy = module.data_plane.bedrock_policy_json
}

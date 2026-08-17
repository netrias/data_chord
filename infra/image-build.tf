resource "aws_ecr_repository" "app" {
  name                 = local.name_prefix
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/aws/codebuild/${local.name_prefix}"
  retention_in_days = 14

  tags = local.common_tags
}

resource "aws_iam_role" "application_build" {
  name                 = "${local.name_prefix}-application-build"
  path                 = var.application_role_path
  permissions_boundary = var.application_role_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "codebuild.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "application_build" {
  name = "${local.name_prefix}-codebuild"
  role = aws_iam_role.application_build.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.codebuild.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeImages",
          "ecr:GetAuthorizationToken",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "codeconnections:GetConnection",
          "codeconnections:GetConnectionToken",
          "codeconnections:UseConnection"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:codeconnections:${var.aws_region}:${data.aws_caller_identity.current.account_id}:connection/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = data.aws_secretsmanager_secret.github_app.arn
      },
    ]
  })
}

resource "aws_codebuild_project" "app_image" {
  name          = "${local.name_prefix}-image"
  description   = "Build and push the Data Chord container image"
  service_role  = aws_iam_role.application_build.arn
  build_timeout = 60

  depends_on = [aws_iam_role_policy.application_build]

  artifacts {
    type = "NO_ARTIFACTS"
  }

  cache {
    type = "LOCAL"
    # Docker and source caches keep app-only deploys fast enough to use as the
    # normal deployment path instead of pushing images from a developer laptop.
    modes = ["LOCAL_DOCKER_LAYER_CACHE", "LOCAL_SOURCE_CACHE"]
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/standard:7.0"
    type                        = "LINUX_CONTAINER"
    privileged_mode             = true
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = data.aws_caller_identity.current.account_id
    }

    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = var.aws_region
    }

    environment_variable {
      name  = "IMAGE_REPO_URI"
      value = aws_ecr_repository.app.repository_url
    }

    environment_variable {
      name  = "IMAGE_REPO_NAME"
      value = aws_ecr_repository.app.name
    }

    environment_variable {
      name  = "GITHUB_APP_SECRET_ARN"
      value = data.aws_secretsmanager_secret.github_app.arn
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.codebuild.name
      status     = "ENABLED"
    }
  }

  source {
    type      = "GITHUB"
    location  = var.application_repository_url
    buildspec = "infra/buildspec.yml"
  }

  tags = local.common_tags
}

data "aws_caller_identity" "current" {}

data "aws_secretsmanager_secret" "netrias_api_key" {
  name = var.netrias_api_key_secret_name
}

locals {
  name_prefix          = substr(lower(replace("${var.project_name}-${var.environment}", "_", "-")), 0, 32)
  hosted_zone_name     = trimsuffix(var.hosted_zone_name, ".")
  use_managed_dns      = var.domain_name == "" && local.hosted_zone_name != ""
  managed_domain_name  = "${var.domain_label != "" ? var.domain_label : local.name_prefix}.${local.hosted_zone_name}"
  app_host             = var.domain_name != "" ? var.domain_name : (local.use_managed_dns ? local.managed_domain_name : aws_lb.app.dns_name)
  app_url              = "https://${local.app_host}"
  callback_url         = "${local.app_url}/oauth2/idpresponse"
  auth_bypass_ready    = length(nonsensitive(var.auth_bypass_cidrs)) > 0
  certificate_arn      = var.certificate_arn != "" ? var.certificate_arn : aws_acm_certificate_validation.app[0].certificate_arn
  invite_environment   = var.environment == "prod" ? "" : " (${var.environment} environment)"
  invite_email_subject = "Your Data Chord${local.invite_environment} access"
  invite_sms_message   = "Data Chord${local.invite_environment}: username {username}, temporary password {####}"
  invite_email_message = templatefile("${path.module}/templates/cognito-invite-email.html.tftpl", {
    app_url            = local.app_url
    invite_environment = local.invite_environment
  })
  common_tags = merge(var.tags, {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "opentofu"
  })
}

data "aws_route53_zone" "app" {
  count = local.use_managed_dns ? 1 : 0

  name         = local.hosted_zone_name
  private_zone = false
}

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public access to the Data Chord load balancer"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTP redirect"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS app access"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_security_group" "task" {
  name        = "${local.name_prefix}-task"
  description = "Only the ALB can reach the app task"
  vpc_id      = var.vpc_id

  ingress {
    description     = "App traffic from ALB"
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Outbound AWS/API access"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_security_group" "secrets_endpoint" {
  name        = "${local.name_prefix}-secrets-endpoint"
  description = "Secrets Manager VPC endpoint access from Data Chord tasks"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Secrets Manager HTTPS from app tasks"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.task.id]
  }

  egress {
    description = "Outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_vpc_endpoint_security_group_association" "secretsmanager_tasks" {
  vpc_endpoint_id   = var.secretsmanager_vpc_endpoint_id
  security_group_id = aws_security_group.secrets_endpoint.id
}

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

resource "aws_s3_bucket_server_side_encryption_configuration" "workflow" {
  bucket = aws_s3_bucket.workflow.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "workflow" {
  bucket = aws_s3_bucket.workflow.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_ecr_repository" "app" {
  name                 = local.name_prefix
  image_tag_mutability = "MUTABLE"
  force_delete         = var.force_delete_repositories

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = 14

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/aws/codebuild/${local.name_prefix}"
  retention_in_days = 14

  tags = local.common_tags
}

resource "aws_iam_role" "task_execution" {
  name = "${local.name_prefix}-task-exec"

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

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "${local.name_prefix}-task-secrets"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = data.aws_secretsmanager_secret.netrias_api_key.arn
    }]
  })
}

resource "aws_iam_role" "task" {
  name = "${local.name_prefix}-task"

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

resource "aws_iam_role_policy" "task_workflow_storage" {
  name = "${local.name_prefix}-workflow-storage"
  role = aws_iam_role.task.id

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
        Resource = "${aws_s3_bucket.workflow.arn}/${var.environment}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.workflow.arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["${var.environment}/*"]
          }
        }
      }
    ]
  })
}

resource "aws_ecs_cluster" "app" {
  name = local.name_prefix

  tags = local.common_tags
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name_prefix
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
      essential = true
      portMappings = [{
        containerPort = var.container_port
        hostPort      = var.container_port
        protocol      = "tcp"
      }]
      environment = [
        {
          name  = "DATA_CHORD_STORAGE"
          value = "s3"
        },
        {
          name  = "DATA_CHORD_S3_BUCKET"
          value = aws_s3_bucket.workflow.bucket
        },
        {
          name  = "DATA_CHORD_S3_PREFIX"
          value = var.environment
        },
        {
          name  = "DATA_CHORD_NETRIAS_ENVIRONMENT"
          value = var.environment
        },
        {
          name  = "DATA_CHORD_NETRIAS_TIMEOUT_SECONDS"
          value = "3600"
        },
        {
          name  = "CORS_ALLOW_ORIGINS"
          value = local.app_url
        }
      ]
      secrets = [{
        name      = "NETRIAS_API_KEY"
        valueFrom = data.aws_secretsmanager_secret.netrias_api_key.arn
      }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.app.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "app"
        }
      }
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:${var.container_port}/healthz', timeout=3).read()\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])

  tags = local.common_tags
}

resource "aws_lb" "app" {
  name               = substr("${local.name_prefix}-alb", 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  tags = local.common_tags
}

resource "aws_lb_target_group" "app" {
  name        = substr("${local.name_prefix}-app", 0, 32)
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    enabled             = true
    path                = "/healthz"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = local.common_tags
}

resource "aws_acm_certificate" "app" {
  count = var.certificate_arn == "" && local.use_managed_dns ? 1 : 0

  domain_name       = local.app_host
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = local.common_tags
}

resource "aws_route53_record" "certificate_validation" {
  count = var.certificate_arn == "" && local.use_managed_dns ? 1 : 0

  allow_overwrite = true
  name            = tolist(aws_acm_certificate.app[0].domain_validation_options)[0].resource_record_name
  records         = [tolist(aws_acm_certificate.app[0].domain_validation_options)[0].resource_record_value]
  ttl             = 60
  type            = tolist(aws_acm_certificate.app[0].domain_validation_options)[0].resource_record_type
  zone_id         = data.aws_route53_zone.app[0].zone_id
}

resource "aws_acm_certificate_validation" "app" {
  count = var.certificate_arn == "" && local.use_managed_dns ? 1 : 0

  certificate_arn         = aws_acm_certificate.app[0].arn
  validation_record_fqdns = [aws_route53_record.certificate_validation[0].fqdn]
}

resource "aws_cognito_user_pool" "auth" {
  name = "${local.name_prefix}-users"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  admin_create_user_config {
    allow_admin_create_user_only = true

    invite_message_template {
      email_message = local.invite_email_message
      email_subject = local.invite_email_subject
      sms_message   = local.invite_sms_message
    }
  }

  tags = local.common_tags
}

resource "aws_cognito_user_pool_domain" "auth" {
  domain       = var.cognito_domain_prefix != "" ? var.cognito_domain_prefix : "${local.name_prefix}-${data.aws_caller_identity.current.account_id}"
  user_pool_id = aws_cognito_user_pool.auth.id
}

resource "aws_cognito_user_pool_client" "alb" {
  name         = "${local.name_prefix}-alb"
  user_pool_id = aws_cognito_user_pool.auth.id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = [local.callback_url]
  logout_urls                          = [local.app_url]

  lifecycle {
    ignore_changes = [generate_secret]
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = local.common_tags
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = local.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  default_action {
    type  = "authenticate-cognito"
    order = 1

    authenticate_cognito {
      user_pool_arn              = aws_cognito_user_pool.auth.arn
      user_pool_client_id        = aws_cognito_user_pool_client.alb.id
      user_pool_domain           = aws_cognito_user_pool_domain.auth.domain
      on_unauthenticated_request = "authenticate"
    }
  }

  default_action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.app.arn
  }

  tags = local.common_tags
}

resource "aws_lb_listener_rule" "auth_bypass" {
  count = local.auth_bypass_ready ? 1 : 0

  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  condition {
    source_ip {
      values = var.auth_bypass_cidrs
    }
  }

  tags = local.common_tags
}

resource "aws_route53_record" "app" {
  count = local.use_managed_dns ? 1 : 0

  zone_id = data.aws_route53_zone.app[0].zone_id
  name    = local.app_host
  type    = "A"

  alias {
    name                   = aws_lb.app.dns_name
    zone_id                = aws_lb.app.zone_id
    evaluate_target_health = true
  }
}

resource "aws_ecs_service" "app" {
  name            = local.name_prefix
  cluster         = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.https]

  tags = local.common_tags
}

resource "aws_iam_role" "codebuild" {
  name = "${local.name_prefix}-codebuild"

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

resource "aws_iam_role_policy" "codebuild" {
  name = "${local.name_prefix}-codebuild"
  role = aws_iam_role.codebuild.id

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
          "ecs:DescribeServices",
          "ecs:UpdateService"
        ]
        Resource = "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.app.name}/${local.name_prefix}"
      }
    ]
  })
}

resource "aws_codebuild_project" "app_image" {
  name         = "${local.name_prefix}-image"
  description  = "Build and deploy the Data Chord container image"
  service_role = aws_iam_role.codebuild.arn

  artifacts {
    type = "NO_ARTIFACTS"
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
      name  = "ECS_CLUSTER_NAME"
      value = aws_ecs_cluster.app.name
    }

    environment_variable {
      name  = "ECS_SERVICE_NAME"
      value = local.name_prefix
    }
  }

  logs_config {
    cloudwatch_logs {
      group_name = aws_cloudwatch_log_group.codebuild.name
      status     = "ENABLED"
    }
  }

  source {
    type      = var.codebuild_source_type
    location  = var.codebuild_source_location
    buildspec = "infra/buildspec.yml"
  }

  tags = local.common_tags
}

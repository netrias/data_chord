resource "aws_security_group" "task" {
  name        = "${local.name_prefix}-task"
  description = "Only the ALB can reach the app task"
  vpc_id      = aws_vpc.app.id

  ingress {
    description     = "App traffic from ALB"
    from_port       = 8000
    to_port         = 8000
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

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = 14

  tags = local.common_tags
}

resource "aws_iam_role" "application_task_execution" {
  name                 = "${local.name_prefix}-application-task-exec"
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

resource "aws_iam_role_policy_attachment" "application_task_execution" {
  role       = aws_iam_role.application_task_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "application_task_execution_secrets" {
  name = "${local.name_prefix}-task-secrets"
  role = aws_iam_role.application_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = data.aws_secretsmanager_secret.netrias_api_key.arn
    }]
  })
}

resource "aws_ecs_cluster" "app" {
  name = local.name_prefix

  tags = local.common_tags
}

resource "aws_ecs_task_definition" "application" {
  family                   = local.name_prefix
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = aws_iam_role.application_task_execution.arn
  task_role_arn            = aws_iam_role.application_task.arn

  depends_on = [
    aws_iam_role_policy.application_task_execution_secrets,
    aws_iam_role_policy.application_task_workflow_storage,
    aws_iam_role_policy.application_task_bedrock_mantle,
    aws_iam_role_policy_attachment.application_task_execution,
  ]

  container_definitions = jsonencode([
    {
      name      = "app"
      image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
      essential = true
      portMappings = [{
        containerPort = 8000
        hostPort      = 8000
        protocol      = "tcp"
      }]
      environment = concat([
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
          value = local.deployment_prefix
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
          name  = "DATA_CHORD_HARMONIZER"
          value = var.harmonizer
        },
        {
          name  = "DATA_CHORD_AGENTIC_WORKERS"
          value = tostring(var.agentic_workers)
        },
        {
          name  = "AWS_REGION"
          value = var.aws_region
        },
        {
          name  = "DATA_CHORD_ASSET_VERSION"
          value = var.image_tag
        },
        {
          name  = "DATA_CHORD_ALB_ARN"
          value = aws_lb.app.arn
        },
        {
          name  = "CORS_ALLOW_ORIGINS"
          value = local.app_url
        }
        ], var.netrias_harmonization_url == "" ? [] : [{
          name  = "DATA_CHORD_NETRIAS_HARMONIZATION_URL"
          value = var.netrias_harmonization_url
      }])
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
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])

  tags = local.common_tags
}

resource "aws_ecs_service" "app" {
  name            = local.name_prefix
  cluster         = aws_ecs_cluster.app.id
  task_definition = aws_ecs_task_definition.application.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = local.public_subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.https]

  tags = local.common_tags
}

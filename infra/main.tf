data "aws_caller_identity" "current" {}

data "aws_secretsmanager_secret" "netrias_api_key" {
  name = var.netrias_api_key_secret_name
}

locals {
  name_prefix      = substr(lower(replace("${var.project_name}-${var.environment}", "_", "-")), 0, 32)
  hosted_zone_name = trimsuffix(var.hosted_zone_name, ".")
  # Prefer a managed subdomain when the caller supplies a hosted zone but not a
  # final domain name; this keeps staging/prod setup small for internal deploys.
  use_managed_dns     = var.domain_name == "" && local.hosted_zone_name != ""
  managed_domain_name = "${var.domain_label != "" ? var.domain_label : local.name_prefix}.${local.hosted_zone_name}"
  app_host            = var.domain_name != "" ? var.domain_name : (local.use_managed_dns ? local.managed_domain_name : aws_lb.app.dns_name)
  app_url             = "https://${local.app_host}"
  callback_url        = "${local.app_url}/oauth2/idpresponse"
  # Omit the bypass listener rule unless ranges exist so hosted auth stays
  # mandatory by default.
  auth_bypass_ready    = length(nonsensitive(var.auth_bypass_cidrs)) > 0
  certificate_arn      = var.certificate_arn != "" ? var.certificate_arn : aws_acm_certificate_validation.app[0].certificate_arn
  invite_environment   = var.environment == "prod" ? "" : " (${var.environment} environment)"
  invite_email_subject = "Your Data Chord${local.invite_environment} access"
  invite_sms_message   = "Data Chord${local.invite_environment}: username {username}, temporary password {####}"
  invite_email_message = templatefile("${path.module}/templates/cognito-invite-email.html.tftpl", {
    app_url            = local.app_url
    invite_environment = local.invite_environment
  })
  alert_actions   = [aws_sns_topic.alerts.arn]
  ecs_service_arn = "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.app.name}/${local.name_prefix}"
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
    # Workflow writes use optimistic version checks, so keeping object versions
    # gives operators a recovery trail when a bad deploy corrupts artifacts.
    status = "Enabled"
  }
}

resource "aws_s3_bucket" "alb_logs" {
  bucket = "${local.name_prefix}-alb-logs-${data.aws_caller_identity.current.account_id}-${var.aws_region}"

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-alb-logs"
  })
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  rule {
    id     = "expire-alb-access-logs"
    status = "Enabled"

    expiration {
      days = var.alb_access_log_retention_days
    }
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowLoadBalancerLogDeliveryWrite"
        Effect = "Allow"
        Principal = {
          Service = "logdelivery.elasticloadbalancing.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.alb_logs.arn}/alb/${var.environment}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
      }
    ]
  })
}

resource "aws_ecr_repository" "app" {
  name                 = local.name_prefix
  image_tag_mutability = "IMMUTABLE"
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

resource "aws_sns_topic" "alerts" {
  name         = "${local.name_prefix}-alerts"
  display_name = "Data Chord ${var.environment} alerts"

  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "alert_email" {
  for_each = toset(var.alert_email_addresses)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_cloudwatch_log_metric_filter" "app_error_logs" {
  name           = "${local.name_prefix}-app-error-logs"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "\"| ERROR |\""

  metric_transformation {
    name      = "${local.name_prefix}-app-error-count"
    namespace = "DataChord/${var.environment}"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "app_error_logs" {
  alarm_name          = "${upper(var.environment)} Data Chord warning - app ERROR logs"
  alarm_description   = "[${upper(var.environment)}] Data Chord app emitted ERROR logs."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = aws_cloudwatch_log_metric_filter.app_error_logs.metric_transformation[0].name
  namespace           = aws_cloudwatch_log_metric_filter.app_error_logs.metric_transformation[0].namespace
  period              = 300
  statistic           = "Sum"
  threshold           = var.app_error_log_alarm_threshold
  treat_missing_data  = "notBreaching"

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "alb_no_healthy_targets" {
  alarm_name          = "${upper(var.environment)} Data Chord DOWN - no healthy app targets"
  alarm_description   = "[${upper(var.environment)}] Data Chord ALB has fewer healthy targets than desired tasks."
  comparison_operator = "LessThanThreshold"
  datapoints_to_alarm = 2
  evaluation_periods  = 2
  metric_name         = "HealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Minimum"
  threshold           = var.desired_count
  treat_missing_data  = "breaching"
  alarm_actions       = local.alert_actions

  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "alb_unhealthy_targets" {
  alarm_name          = "${upper(var.environment)} Data Chord DEGRADED - unhealthy app targets"
  alarm_description   = "[${upper(var.environment)}] Data Chord ALB target group has unhealthy targets."
  comparison_operator = "GreaterThanThreshold"
  datapoints_to_alarm = 2
  evaluation_periods  = 2
  metric_name         = "UnHealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Minimum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alert_actions

  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "app_5xx" {
  alarm_name          = "${upper(var.environment)} Data Chord USER ERRORS - app returned 5xx"
  alarm_description   = "[${upper(var.environment)}] Data Chord app targets returned 5xx responses."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = var.app_5xx_alarm_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alert_actions

  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${upper(var.environment)} Data Chord ALB ERRORS - load balancer returned 5xx"
  alarm_description   = "[${upper(var.environment)}] Data Chord ALB generated 5xx responses."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_ELB_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = var.alb_5xx_alarm_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alert_actions

  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "alb_auth_errors" {
  alarm_name          = "${upper(var.environment)} Data Chord AUTH ERRORS - ALB authentication flow failed"
  alarm_description   = "[${upper(var.environment)}] Data Chord ALB could not complete an authentication flow. Check ALB access log error_reason for details."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ELBAuthError"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = var.alb_auth_alarm_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alert_actions

  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "alb_auth_failures" {
  alarm_name          = "${upper(var.environment)} Data Chord AUTH FAILURES - user authentication failed"
  alarm_description   = "[${upper(var.environment)}] Data Chord ALB authentication failed because the IdP denied access or an authorization code was reused. Check ALB access log error_reason for details."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ELBAuthFailure"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = var.alb_auth_alarm_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alert_actions

  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "target_connection_errors" {
  alarm_name          = "${upper(var.environment)} Data Chord DOWN - ALB cannot reach app"
  alarm_description   = "[${upper(var.environment)}] Data Chord ALB could not connect to app targets."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "TargetConnectionErrorCount"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = var.target_connection_error_alarm_threshold
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alert_actions

  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "target_response_time" {
  alarm_name          = "${upper(var.environment)} Data Chord warning - high p95 response time"
  alarm_description   = "[${upper(var.environment)}] Data Chord p95 app target response time is high."
  comparison_operator = "GreaterThanThreshold"
  datapoints_to_alarm = 2
  evaluation_periods  = 3
  extended_statistic  = "p95"
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  threshold           = var.target_response_time_alarm_seconds
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.app.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "ecs_cpu_high" {
  alarm_name          = "${upper(var.environment)} Data Chord warning - ECS CPU high"
  alarm_description   = "[${upper(var.environment)}] Data Chord ECS service CPU utilization is high."
  comparison_operator = "GreaterThanThreshold"
  datapoints_to_alarm = 3
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = var.ecs_cpu_alarm_threshold_percent
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.app.name
    ServiceName = local.name_prefix
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "ecs_memory_high" {
  alarm_name          = "${upper(var.environment)} Data Chord warning - ECS memory high"
  alarm_description   = "[${upper(var.environment)}] Data Chord ECS service memory utilization is high."
  comparison_operator = "GreaterThanThreshold"
  datapoints_to_alarm = 3
  evaluation_periods  = 3
  metric_name         = "MemoryUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = var.ecs_memory_alarm_threshold_percent
  treat_missing_data  = "notBreaching"

  dimensions = {
    ClusterName = aws_ecs_cluster.app.name
    ServiceName = local.name_prefix
  }

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
        # App code prefixes every workflow key by environment; IAM repeats that
        # boundary so a task cannot cross-read another environment's artifacts.
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
          name = "DATA_CHORD_S3_PREFIX"
          # Prefix storage by environment inside one bucket so staging and prod
          # can share bucket policy shape while keeping object namespaces apart.
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
          name  = "DATA_CHORD_ALB_ARN"
          value = aws_lb.app.arn
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

  access_logs {
    bucket  = aws_s3_bucket.alb_logs.bucket
    prefix  = "alb/${var.environment}"
    enabled = var.enable_alb_access_logs
  }

  tags = local.common_tags

  depends_on = [aws_s3_bucket_policy.alb_logs]
}

resource "aws_lb_target_group" "app" {
  name                 = substr("${local.name_prefix}-app", 0, 32)
  port                 = var.container_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = var.target_group_deregistration_delay_seconds

  health_check {
    enabled             = true
    path                = "/healthz"
    matcher             = "200"
    interval            = var.target_group_health_check_interval_seconds
    timeout             = 5
    healthy_threshold   = var.target_group_healthy_threshold
    unhealthy_threshold = var.target_group_unhealthy_threshold
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
    # This rule exists for trusted networks such as VPNs during onboarding and
    # incident recovery; normal public traffic still goes through Cognito.
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

resource "aws_cloudwatch_event_rule" "ecs_service_error" {
  name        = "${local.name_prefix}-ecs-service-error"
  description = "Alert when the Data Chord ${var.environment} ECS service emits an ERROR action event."

  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Service Action"]
    resources     = [local.ecs_service_arn]
    detail = {
      clusterArn = [aws_ecs_cluster.app.arn]
      eventType  = ["ERROR"]
    }
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "ecs_service_error_alert" {
  rule = aws_cloudwatch_event_rule.ecs_service_error.name
  arn  = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths = {
      event_name = "$.detail.eventName"
      reason     = "$.detail.reason"
      time       = "$.time"
    }
    input_template = "\"[${upper(var.environment)}] Data Chord ECS service error: <event_name> at <time>. Reason: <reason>.\""
  }
}

resource "aws_cloudwatch_event_rule" "ecs_deployment_failed" {
  name        = "${local.name_prefix}-ecs-deployment-failed"
  description = "Alert when the Data Chord ${var.environment} ECS deployment circuit breaker reports failure."

  event_pattern = jsonencode({
    source        = ["aws.ecs"]
    "detail-type" = ["ECS Deployment State Change"]
    resources     = [local.ecs_service_arn]
    detail = {
      eventName = ["SERVICE_DEPLOYMENT_FAILED"]
      eventType = ["ERROR"]
    }
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "ecs_deployment_failed_alert" {
  rule = aws_cloudwatch_event_rule.ecs_deployment_failed.name
  arn  = aws_sns_topic.alerts.arn

  input_transformer {
    input_paths = {
      deployment_id = "$.detail.deploymentId"
      reason        = "$.detail.reason"
      time          = "$.time"
    }
    input_template = "\"[${upper(var.environment)}] Data Chord ECS deployment failed: deployment <deployment_id> at <time>. Reason: <reason>.\""
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowAccountOwnerManageTopic"
        Effect = "Allow"
        Principal = {
          AWS = "*"
        }
        Action = [
          "SNS:AddPermission",
          "SNS:DeleteTopic",
          "SNS:GetTopicAttributes",
          "SNS:ListSubscriptionsByTopic",
          "SNS:Publish",
          "SNS:RemovePermission",
          "SNS:SetTopicAttributes",
          "SNS:Subscribe"
        ]
        Resource = aws_sns_topic.alerts.arn
        Condition = {
          StringEquals = {
            "AWS:SourceOwner" = data.aws_caller_identity.current.account_id
          }
        }
      },
      {
        Sid    = "AllowCloudWatchAlarmsPublish"
        Effect = "Allow"
        Principal = {
          Service = "cloudwatch.amazonaws.com"
        }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.alerts.arn
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      },
      {
        Sid    = "AllowEventBridgePublish"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.alerts.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = [
              aws_cloudwatch_event_rule.ecs_deployment_failed.arn,
              aws_cloudwatch_event_rule.ecs_service_error.arn
            ]
          }
        }
      }
    ]
  })
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
          "ecr:DescribeImages",
          "ecr:GetAuthorizationToken",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_codebuild_project" "app_image" {
  name         = "${local.name_prefix}-image"
  description  = "Build and push the Data Chord container image"
  service_role = aws_iam_role.codebuild.arn

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

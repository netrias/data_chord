data "aws_route53_zone" "app" {
  name         = local.hosted_zone_name
  private_zone = false
}

moved {
  from = aws_acm_certificate.app[0]
  to   = aws_acm_certificate.app
}

moved {
  from = aws_route53_record.certificate_validation[0]
  to   = aws_route53_record.certificate_validation
}

moved {
  from = aws_acm_certificate_validation.app[0]
  to   = aws_acm_certificate_validation.app
}

moved {
  from = aws_route53_record.app[0]
  to   = aws_route53_record.app
}

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public access to the Data Chord load balancer"
  vpc_id      = aws_vpc.app.id

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

resource "aws_lb" "app" {
  # The new name forces the one-time move from the shared VPC to this app VPC.
  name               = substr("${local.name_prefix}-web", 0, 32)
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = local.public_subnet_ids

  lifecycle {
    # AWS cannot move an existing load balancer between VPCs. The provider
    # otherwise plans the subnet change in place and the AWS API rejects it.
    replace_triggered_by = [aws_vpc.app.id]
  }

  tags = local.common_tags
}

resource "aws_lb_target_group" "app" {
  name                 = substr("${local.name_prefix}-app", 0, 32)
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.app.id
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
  domain_name       = local.app_host
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = local.common_tags
}

resource "aws_route53_record" "certificate_validation" {
  allow_overwrite = true
  name            = tolist(aws_acm_certificate.app.domain_validation_options)[0].resource_record_name
  records         = [tolist(aws_acm_certificate.app.domain_validation_options)[0].resource_record_value]
  ttl             = 60
  type            = tolist(aws_acm_certificate.app.domain_validation_options)[0].resource_record_type
  zone_id         = data.aws_route53_zone.app.zone_id
}

resource "aws_acm_certificate_validation" "app" {
  certificate_arn         = aws_acm_certificate.app.arn
  validation_record_fqdns = [aws_route53_record.certificate_validation.fqdn]
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
  domain       = "${local.name_prefix}-${data.aws_caller_identity.current.account_id}"
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

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = aws_acm_certificate_validation.app.certificate_arn
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

resource "aws_route53_record" "app" {
  zone_id = data.aws_route53_zone.app.zone_id
  name    = local.app_host
  type    = "A"

  alias {
    name                   = aws_lb.app.dns_name
    zone_id                = aws_lb.app.zone_id
    evaluate_target_health = true
  }
}

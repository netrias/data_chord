data "aws_partition" "current" {}

data "aws_caller_identity" "current" {
  lifecycle {
    postcondition {
      condition     = self.account_id == var.expected_account_id
      error_message = "AWS credentials do not resolve to the target account."
    }
  }
}

data "aws_secretsmanager_secret" "netrias_api_key" {
  name = coalesce(var.netrias_api_key_secret_name, "data-chord/${var.environment}/netrias-api-key")
}

data "aws_secretsmanager_secret" "github_app" {
  name = var.github_app_secret_name
}

locals {
  name_prefix          = "data-chord-${var.environment}"
  deployment_prefix    = "datachord/${var.deployment_target}/${var.environment}"
  hosted_zone_name     = trimsuffix(var.hosted_zone_name, ".")
  app_host             = "${var.domain_label}.${local.hosted_zone_name}"
  app_url              = "https://${local.app_host}"
  callback_url         = "${local.app_url}/oauth2/idpresponse"
  invite_environment   = var.environment == "prod" ? "" : " (${var.environment} environment)"
  invite_email_subject = "Your Data Chord${local.invite_environment} access"
  invite_sms_message   = "Data Chord${local.invite_environment}: username {username}, temporary password {####}"
  invite_email_message = templatefile("${path.module}/templates/cognito-invite-email.html.tftpl", {
    app_url            = local.app_url
    invite_environment = local.invite_environment
  })
  common_tags = {
    Project     = "data-chord"
    Environment = var.environment
    ManagedBy   = "opentofu"
  }
}

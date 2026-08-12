variable "target_slug" {
  description = "Foundation target that owns this deployment."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]*$", var.target_slug))
    error_message = "target_slug must be a lowercase slug."
  }
}

variable "aws_partition" {
  description = "AWS partition selected by the foundation contract."
  type        = string

  validation {
    condition     = contains(["aws", "aws-us-gov"], var.aws_partition)
    error_message = "aws_partition must be aws or aws-us-gov."
  }
}

variable "expected_account_id" {
  description = "AWS account that owns this application deployment."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must be a 12-digit AWS account id."
  }
}

variable "application_role_boundary_arn" {
  description = "Foundation-owned permission boundary applied to Data Chord application roles."
  type        = string

  validation {
    condition     = can(regex("^arn:${var.aws_partition}:iam::${var.expected_account_id}:policy/.+$", var.application_role_boundary_arn))
    error_message = "application_role_boundary_arn must be an IAM policy in expected_account_id."
  }
}

variable "application_role_path" {
  description = "Foundation-owned IAM path for Data Chord application roles."
  type        = string

  validation {
    condition     = can(regex("^/.+/$", var.application_role_path))
    error_message = "application_role_path must start and end with a slash."
  }
}

variable "environment" {
  description = "Deployment environment name, such as staging or prod."
  type        = string

  validation {
    condition     = contains(["dev", "qa", "staging", "prod"], var.environment)
    error_message = "environment must be dev, qa, staging, or prod."
  }
}

variable "aws_region" {
  description = "AWS region for the app."
  type        = string
}

variable "netrias_environment" {
  description = "Netrias service environment used by the application."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.netrias_environment)
    error_message = "netrias_environment must be staging or prod."
  }
}

variable "data_model_store_url" {
  description = "Data Model Store URL published by the foundation contract."
  type        = string

  validation {
    condition     = can(regex("^https://[^/[:space:]]+", var.data_model_store_url))
    error_message = "data_model_store_url must be an HTTPS URL."
  }
}

variable "certificate_arn" {
  description = "Optional ACM certificate ARN for the HTTPS listener. Leave empty to create and validate one in hosted_zone_name."
  type        = string
  default     = ""

  validation {
    condition = (
      (var.certificate_arn == "" && var.domain_name == "" && trimspace(trimsuffix(var.hosted_zone_name, ".")) != "") ||
      (
        trimspace(var.certificate_arn) != "" && var.certificate_arn == trimspace(var.certificate_arn) &&
        trimspace(var.domain_name) != "" && var.domain_name == trimspace(var.domain_name) &&
        var.hosted_zone_name == ""
      )
    )
    error_message = "Choose one TLS mode: leave certificate_arn and domain_name empty and provide hosted_zone_name, or leave hosted_zone_name empty and provide nonblank certificate_arn and domain_name values without surrounding whitespace."
  }
}

variable "domain_name" {
  description = "Optional DNS name users will visit. Leave empty to generate one under hosted_zone_name."
  type        = string
  default     = ""
}

variable "hosted_zone_name" {
  description = "Route 53 hosted zone used to generate an obscure app hostname and validate ACM. Leave empty only if certificate_arn and domain_name are both supplied."
  type        = string
  default     = ""
}

variable "domain_label" {
  description = "Optional left-hand DNS label under hosted_zone_name. Leave empty to generate data-chord-<environment>-<random>."
  type        = string
  default     = ""
}

variable "netrias_api_key_secret_name" {
  description = "Secrets Manager secret name containing NETRIAS_API_KEY."
  type        = string
}

variable "netrias_harmonization_url" {
  description = "Optional Netrias harmonization endpoint override."
  type        = string
  default     = ""
}

variable "auth_bypass_cidrs" {
  description = "Trusted source CIDRs that can reach the app without Cognito auth. Loaded by deploy scripts from Secrets Manager."
  type        = list(string)
  default     = []
  sensitive   = true
}

variable "desired_count" {
  description = "Number of Fargate tasks to run."
  type        = number
  default     = 1
}

variable "alert_email_addresses" {
  description = "Email addresses subscribed to environment-specific health alerts. Leave empty to create alarms without email subscribers."
  type        = list(string)
  default     = []
}

variable "app_5xx_alarm_threshold" {
  description = "Number of target-generated 5xx responses in five minutes before alerting."
  type        = number
  default     = 1
}

variable "alb_5xx_alarm_threshold" {
  description = "Number of load-balancer-generated 5xx responses in five minutes before alerting."
  type        = number
  default     = 1
}

variable "alb_auth_alarm_threshold" {
  description = "Number of ALB authentication errors or failures in five minutes before alerting."
  type        = number
  default     = 1
}

variable "target_connection_error_alarm_threshold" {
  description = "Number of ALB target connection errors in five minutes before alerting."
  type        = number
  default     = 1
}

variable "target_response_time_alarm_seconds" {
  description = "p95 ALB target response time in seconds before alerting."
  type        = number
  default     = 10
}

variable "ecs_cpu_alarm_threshold_percent" {
  description = "Average ECS service CPU utilization percentage before alerting."
  type        = number
  default     = 85
}

variable "ecs_memory_alarm_threshold_percent" {
  description = "Average ECS service memory utilization percentage before alerting."
  type        = number
  default     = 85
}

variable "app_error_log_alarm_threshold" {
  description = "Number of app ERROR log entries in five minutes before alerting."
  type        = number
  default     = 1
}

variable "enable_alb_access_logs" {
  description = "Whether the ALB should write access logs to the environment log bucket."
  type        = bool
  default     = true
}

variable "alb_access_log_retention_days" {
  description = "Days to retain ALB access logs."
  type        = number
  default     = 30

  validation {
    condition     = var.alb_access_log_retention_days >= 1 && var.alb_access_log_retention_days <= 365
    error_message = "alb_access_log_retention_days must be between 1 and 365."
  }
}

variable "container_cpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 1024
}

variable "container_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 2048
}

variable "container_port" {
  description = "Port served by uvicorn in the container."
  type        = number
  default     = 8000
}

variable "image_tag" {
  description = "Immutable image tag the ECS task definition should run. Deploy scripts pass the short source commit SHA."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.image_tag)) && var.image_tag != "latest"
    error_message = "image_tag must be an immutable Docker tag, such as a short commit SHA, and must not be latest."
  }
}

variable "target_group_deregistration_delay_seconds" {
  description = "Seconds the target group waits before deregistered targets stop receiving traffic."
  type        = number
  default     = 300

  validation {
    condition     = var.target_group_deregistration_delay_seconds >= 0 && var.target_group_deregistration_delay_seconds <= 3600
    error_message = "target_group_deregistration_delay_seconds must be between 0 and 3600."
  }
}

variable "target_group_health_check_interval_seconds" {
  description = "Seconds between ALB target group health checks."
  type        = number
  default     = 30

  validation {
    condition     = var.target_group_health_check_interval_seconds >= 5 && var.target_group_health_check_interval_seconds <= 300
    error_message = "target_group_health_check_interval_seconds must be between 5 and 300."
  }
}

variable "target_group_healthy_threshold" {
  description = "Consecutive successful health checks required before a target is healthy."
  type        = number
  default     = 2

  validation {
    condition     = var.target_group_healthy_threshold >= 2 && var.target_group_healthy_threshold <= 10
    error_message = "target_group_healthy_threshold must be between 2 and 10."
  }
}

variable "target_group_unhealthy_threshold" {
  description = "Consecutive failed health checks required before a target is unhealthy."
  type        = number
  default     = 3

  validation {
    condition     = var.target_group_unhealthy_threshold >= 2 && var.target_group_unhealthy_threshold <= 10
    error_message = "target_group_unhealthy_threshold must be between 2 and 10."
  }
}

variable "cognito_domain_prefix" {
  description = "Optional globally unique Cognito hosted UI domain prefix. Leave empty to use a generated prefix."
  type        = string
  default     = ""
}

variable "force_delete_repositories" {
  description = "Allow OpenTofu destroy to delete non-empty ECR repositories. Keep false for normal use."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags applied to AWS resources."
  type        = map(string)
  default     = {}
}

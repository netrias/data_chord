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
    condition     = can(regex("^arn:(aws|aws-us-gov):iam::${var.expected_account_id}:policy/.+$", var.application_role_boundary_arn))
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
  description = "Deployment environment name."
  type        = string

  validation {
    condition     = contains(["dev", "qa", "staging", "prod"], var.environment)
    error_message = "environment must be dev, qa, staging, or prod."
  }
}

variable "deployment_target" {
  description = "Stable target name used in deployment-owned storage paths."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]*$", var.deployment_target))
    error_message = "deployment_target must be a lowercase target name."
  }
}

variable "aws_region" {
  description = "AWS region for the app."
  type        = string
}

variable "hosted_zone_name" {
  description = "Public Route 53 hosted zone used for the app hostname and certificate validation."
  type        = string

  validation {
    condition     = trimspace(trimsuffix(var.hosted_zone_name, ".")) != ""
    error_message = "hosted_zone_name must not be empty."
  }
}

variable "domain_label" {
  description = "App DNS label under hosted_zone_name."
  type        = string

  validation {
    condition     = trimspace(var.domain_label) != ""
    error_message = "domain_label must not be empty."
  }
}

variable "agentic_workers" {
  description = "Maximum concurrent agentic term harmonizations in one task."
  type        = number
  default     = 100

  validation {
    condition     = var.agentic_workers >= 1 && var.agentic_workers <= 100
    error_message = "agentic_workers must be between 1 and 100."
  }
}

variable "container_cpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 4096
}

variable "container_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 8192
}

variable "github_app_secret_name" {
  description = "Secrets Manager name for the read-only GitHub App build credential."
  type        = string
  default     = "data-chord/build/github-app"
}

variable "application_repository_url" {
  description = "HTTPS Git repository that CodeBuild checks out."
  type        = string
  default     = "https://github.com/netrias/data_chord.git"

  validation {
    condition = (
      can(regex("^https://[^/@?#]+/.+\\.git$", var.application_repository_url)) &&
      !strcontains(var.application_repository_url, "?") &&
      !strcontains(var.application_repository_url, "#")
    )
    error_message = "application_repository_url must be a credential-free HTTPS Git URL ending in .git."
  }
}

variable "image_tag" {
  description = "Full Git commit used as the immutable application image tag."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.image_tag))
    error_message = "image_tag must be a full 40-character lowercase Git commit."
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

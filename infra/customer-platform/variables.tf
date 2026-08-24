variable "expected_account_id" {
  description = "AWS account that owns this deployment."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must be a 12-digit AWS account id."
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
  description = "AWS region for the data plane."
  type        = string
}

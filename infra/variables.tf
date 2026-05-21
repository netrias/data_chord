variable "project_name" {
  description = "Short project name used in AWS resource names."
  type        = string
  default     = "data-chord"
}

variable "environment" {
  description = "Deployment environment name, such as staging or prod."
  type        = string
  default     = "staging"
}

variable "aws_region" {
  description = "AWS region for the app."
  type        = string
  default     = "us-east-2"
}

variable "vpc_id" {
  description = "Existing VPC id for the ALB and Fargate service."
  type        = string
}

variable "public_subnet_ids" {
  description = "Existing public subnet ids for the ALB and Fargate service. Use at least two availability zones."
  type        = list(string)
}

variable "secretsmanager_vpc_endpoint_id" {
  description = "Existing Secrets Manager VPC endpoint id used by Fargate tasks to fetch NETRIAS_API_KEY."
  type        = string
}

variable "certificate_arn" {
  description = "Optional ACM certificate ARN for the HTTPS listener. Leave empty to create and validate one in hosted_zone_name."
  type        = string
  default     = ""
}

variable "domain_name" {
  description = "Optional DNS name users will visit. Leave empty to generate one under hosted_zone_name."
  type        = string
  default     = ""
}

variable "hosted_zone_name" {
  description = "Route 53 hosted zone used to generate an obscure app hostname and validate ACM. Leave empty only if certificate_arn and domain_name are both supplied."
  type        = string
  default     = "netriasbdf.cloud"
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
  description = "Image tag the ECS task definition should run. CodeBuild updates the service after pushing latest."
  type        = string
  default     = "latest"
}

variable "codebuild_source_type" {
  description = "CodeBuild source type. GITHUB is the simplest default for the public repo."
  type        = string
  default     = "GITHUB"
}

variable "codebuild_source_location" {
  description = "Repository URL CodeBuild should build."
  type        = string
  default     = "https://github.com/netrias/data_chord.git"
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

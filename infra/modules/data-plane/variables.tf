variable "account_id" {
  description = "AWS account that owns the data plane."
  type        = string
}

variable "aws_region" {
  description = "AWS region for the data plane."
  type        = string
}

variable "common_tags" {
  description = "Tags applied to all data-plane resources."
  type        = map(string)
}

variable "deployment_prefix" {
  description = "S3 prefix that isolates this deployment."
  type        = string
}

variable "name_prefix" {
  description = "Stable prefix for resource names."
  type        = string
}

variable "partition" {
  description = "AWS partition for policy ARNs."
  type        = string
}

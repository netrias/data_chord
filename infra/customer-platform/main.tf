data "aws_partition" "current" {}

data "aws_caller_identity" "current" {
  lifecycle {
    postcondition {
      condition     = self.account_id == var.expected_account_id
      error_message = "AWS credentials do not resolve to the target account."
    }
  }
}

locals {
  name_prefix       = "data-chord-${var.environment}"
  deployment_prefix = "datachord/${var.deployment_target}/${var.environment}"
  common_tags = {
    Project     = "data-chord"
    Environment = var.environment
    ManagedBy   = "opentofu"
  }
}

module "data_plane" {
  source = "../modules/data-plane"

  account_id        = data.aws_caller_identity.current.account_id
  aws_region        = var.aws_region
  common_tags       = local.common_tags
  deployment_prefix = local.deployment_prefix
  name_prefix       = local.name_prefix
  partition         = data.aws_partition.current.partition
}

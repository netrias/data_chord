output "runtime_environment" {
  description = "Environment variables for the customer-hosted Data Chord container."
  value = {
    AWS_REGION                                = var.aws_region
    DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE = module.data_plane.cde_recommendation_cache_table_name
    DATA_CHORD_HARMONIZATION_CACHE_TABLE      = module.data_plane.harmonization_cache_table_name
    DATA_CHORD_IDENTITY_SOURCE                = "trusted_proxy"
    DATA_CHORD_PROFILE                        = "hosted"
    DATA_CHORD_REFERENCE_TABLE                = module.data_plane.reference_data_table_name
    DATA_CHORD_S3_BUCKET                      = module.data_plane.workflow_bucket_name
    DATA_CHORD_S3_PREFIX                      = local.deployment_prefix
    DATA_CHORD_STORAGE                        = "s3"
  }
}

output "runtime_data_policy_json" {
  description = "IAM policy for the customer-owned workload role."
  value       = module.data_plane.runtime_data_policy_json
}

output "bedrock_policy_json" {
  description = "Optional IAM policy for Bedrock Mantle calls."
  value       = module.data_plane.bedrock_policy_json
}

output "reference_loader_policy_json" {
  description = "IAM policy for a customer-owned reference-data loader role."
  value       = module.data_plane.reference_loader_policy_json
}

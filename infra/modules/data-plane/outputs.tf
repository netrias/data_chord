output "workflow_bucket_name" {
  value = aws_s3_bucket.workflow.bucket
}

output "workflow_bucket_arn" {
  value = aws_s3_bucket.workflow.arn
}

output "reference_data_table_name" {
  value = aws_dynamodb_table.reference_data.name
}

output "harmonization_cache_table_name" {
  value = aws_dynamodb_table.harmonization_cache.name
}

output "cde_recommendation_cache_table_name" {
  value = aws_dynamodb_table.cde_recommendation_cache.name
}

output "workflow_policy_json" {
  value = jsonencode(local.workflow_policy)
}

output "reference_policy_json" {
  value = jsonencode(local.reference_policy)
}

output "harmonization_cache_policy_json" {
  value = jsonencode(local.harmonization_cache_policy)
}

output "cde_cache_policy_json" {
  value = jsonencode(local.cde_cache_policy)
}

output "runtime_data_policy_json" {
  value = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      local.workflow_policy.Statement,
      local.reference_policy.Statement,
      local.harmonization_cache_policy.Statement,
      local.cde_cache_policy.Statement,
    )
  })
}

output "bedrock_policy_json" {
  value = jsonencode(local.bedrock_policy)
}

output "reference_loader_policy_json" {
  value = jsonencode(local.reference_loader_policy)
}

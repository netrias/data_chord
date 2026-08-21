mock_provider "aws" {}

variables {
  account_id        = "945365518758"
  aws_region        = "us-east-2"
  common_tags       = { Project = "data-chord" }
  deployment_prefix = "datachord/netrias/staging"
  name_prefix       = "data-chord-staging"
  partition         = "aws"
}

run "data_plane_is_five_managed_resources" {
  command = plan

  assert {
    condition = (
      aws_s3_bucket.workflow.bucket == "data-chord-staging-workflow-945365518758-us-east-2" &&
      aws_s3_bucket_public_access_block.workflow.block_public_acls &&
      aws_s3_bucket_public_access_block.workflow.block_public_policy &&
      aws_s3_bucket_public_access_block.workflow.ignore_public_acls &&
      aws_s3_bucket_public_access_block.workflow.restrict_public_buckets &&
      aws_dynamodb_table.reference_data.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.reference_data.hash_key == "pk" &&
      aws_dynamodb_table.reference_data.range_key == "sk" &&
      aws_dynamodb_table.reference_data.point_in_time_recovery[0].enabled &&
      aws_dynamodb_table.reference_data.deletion_protection_enabled &&
      length(aws_dynamodb_table.reference_data.global_secondary_index) == 0 &&
      aws_dynamodb_table.harmonization_cache.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.harmonization_cache.hash_key == "cache_key" &&
      aws_dynamodb_table.harmonization_cache.point_in_time_recovery[0].enabled &&
      aws_dynamodb_table.harmonization_cache.deletion_protection_enabled &&
      length(aws_dynamodb_table.harmonization_cache.global_secondary_index) == 0 &&
      aws_dynamodb_table.cde_recommendation_cache.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.cde_recommendation_cache.hash_key == "cache_key" &&
      aws_dynamodb_table.cde_recommendation_cache.ttl[0].enabled
    )
    error_message = "The data plane must remain one private S3 bucket boundary and three DynamoDB tables."
  }

  assert {
    condition = (
      jsondecode(output.runtime_data_policy_json).Statement[0].Resource == "${aws_s3_bucket.workflow.arn}/datachord/netrias/staging/*" &&
      toset(jsondecode(output.reference_policy_json).Statement[0].Action) == toset([
        "dynamodb:DescribeTable",
        "dynamodb:Query",
      ])
    )
    error_message = "Runtime access must stay inside the deployment S3 prefix and keep reference data read-only."
  }
}

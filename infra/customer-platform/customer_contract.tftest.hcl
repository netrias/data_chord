mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "945365518758"
    }
  }
}

variables {
  aws_region          = "us-east-2"
  deployment_target   = "netrias"
  environment         = "staging"
  expected_account_id = "945365518758"
}

run "customer_platform_creates_only_the_hosted_data_plane" {
  command = plan

  assert {
    condition = (
      output.runtime_environment.DATA_CHORD_S3_BUCKET == "data-chord-staging-workflow-945365518758-us-east-2" &&
      output.runtime_environment.DATA_CHORD_REFERENCE_TABLE == "data-chord-staging-reference-data" &&
      output.runtime_environment.DATA_CHORD_HARMONIZATION_CACHE_TABLE == "data-chord-staging-harmonization-cache" &&
      output.runtime_environment.DATA_CHORD_CDE_RECOMMENDATION_CACHE_TABLE == "data-chord-staging-cde-recommendation-cache"
    )
    error_message = "Customer platform must create one private workflow bucket and the three required DynamoDB tables."
  }

  assert {
    condition = (
      output.runtime_environment.DATA_CHORD_PROFILE == "hosted" &&
      output.runtime_environment.DATA_CHORD_IDENTITY_SOURCE == "trusted_proxy" &&
      output.runtime_environment.DATA_CHORD_STORAGE == "s3" &&
      output.runtime_environment.DATA_CHORD_S3_PREFIX == "datachord/netrias/staging"
    )
    error_message = "Customer platform must publish the complete hosted runtime boundary."
  }

  assert {
    condition = (
      length(jsondecode(output.runtime_data_policy_json).Statement) == 5 &&
      length(jsondecode(output.bedrock_policy_json).Statement) == 2 &&
      toset(jsondecode(output.reference_loader_policy_json).Statement[0].Action) == toset([
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query",
      ])
    )
    error_message = "Customer platform must publish workload, Bedrock, and reference-loader policies without creating IAM resources."
  }
}

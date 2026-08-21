module "data_plane" {
  source = "./modules/data-plane"

  account_id        = data.aws_caller_identity.current.account_id
  aws_region        = var.aws_region
  common_tags       = local.common_tags
  deployment_prefix = local.deployment_prefix
  name_prefix       = local.name_prefix
  partition         = data.aws_partition.current.partition
}

moved {
  from = aws_s3_bucket.workflow
  to   = module.data_plane.aws_s3_bucket.workflow
}

moved {
  from = aws_s3_bucket_public_access_block.workflow
  to   = module.data_plane.aws_s3_bucket_public_access_block.workflow
}

moved {
  from = aws_dynamodb_table.reference_data
  to   = module.data_plane.aws_dynamodb_table.reference_data
}

moved {
  from = aws_dynamodb_table.harmonization_cache
  to   = module.data_plane.aws_dynamodb_table.harmonization_cache
}

moved {
  from = aws_dynamodb_table.cde_recommendation_cache
  to   = module.data_plane.aws_dynamodb_table.cde_recommendation_cache
}

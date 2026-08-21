output "app_url" {
  description = "URL for the authenticated app."
  value       = local.app_url
}

output "workflow_bucket" {
  description = "S3 bucket used for durable workflow storage."
  value       = module.data_plane.workflow_bucket_name
}

output "reference_data_table" {
  description = "DynamoDB table used for standard metadata and permissible values."
  value       = module.data_plane.reference_data_table_name
}

output "harmonization_cache_table" {
  description = "DynamoDB table used to reuse versioned harmonization results."
  value       = module.data_plane.harmonization_cache_table_name
}

output "cde_recommendation_cache_table" {
  description = "DynamoDB table used to reuse versioned CDE recommendations."
  value       = module.data_plane.cde_recommendation_cache_table_name
}

output "ecr_repository_url" {
  description = "ECR repository URI for the app image."
  value       = aws_ecr_repository.app.repository_url
}

output "codebuild_project_name" {
  description = "CodeBuild project that tests, builds, and pushes the app image."
  value       = aws_codebuild_project.app_image.name
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.app.name
}

output "ecs_service_name" {
  description = "ECS service name."
  value       = aws_ecs_service.app.name
}

output "target_group_arn" {
  description = "ALB target group ARN for task health checks."
  value       = aws_lb_target_group.app.arn
}

output "deployed_image_tag" {
  description = "Immutable image tag recorded in the OpenTofu-managed ECS task definition."
  value       = var.image_tag
}

output "cognito_user_pool_id" {
  description = "Cognito user pool id for inviting app users."
  value       = aws_cognito_user_pool.auth.id
}

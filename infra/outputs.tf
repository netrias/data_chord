output "app_url" {
  description = "URL for the authenticated app."
  value       = local.app_url
}

output "alb_dns_name" {
  description = "DNS name of the application load balancer."
  value       = aws_lb.app.dns_name
}

output "app_hostname" {
  description = "Hostname users should visit."
  value       = local.app_host
}

output "workflow_bucket" {
  description = "S3 bucket used for durable workflow storage."
  value       = aws_s3_bucket.workflow.bucket
}

output "ecr_repository_url" {
  description = "ECR repository URI for the app image."
  value       = aws_ecr_repository.app.repository_url
}

output "codebuild_project_name" {
  description = "CodeBuild project that tests, builds, and pushes the app image."
  value       = aws_codebuild_project.app_image.name
}

output "codebuild_log_group" {
  description = "CloudWatch log group for CodeBuild deploy logs."
  value       = aws_cloudwatch_log_group.codebuild.name
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.app.name
}

output "ecs_service_name" {
  description = "ECS service name."
  value       = local.name_prefix
}

output "ecs_log_group" {
  description = "CloudWatch log group for ECS app logs."
  value       = aws_cloudwatch_log_group.app.name
}

output "alert_topic_arn" {
  description = "SNS topic ARN for environment-specific Data Chord health alerts."
  value       = aws_sns_topic.alerts.arn
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

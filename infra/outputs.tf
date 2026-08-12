output "app_url" {
  description = "URL for the authenticated app."
  value       = local.app_url
}

output "deployed_image_tag" {
  description = "Immutable image tag recorded in the OpenTofu-managed ECS task definition."
  value       = var.image_tag
}

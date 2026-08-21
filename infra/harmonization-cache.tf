resource "aws_iam_role_policy" "application_task_harmonization_cache" {
  name = "${local.name_prefix}-harmonization-cache"
  role = aws_iam_role.application_task.id

  policy = module.data_plane.harmonization_cache_policy_json
}

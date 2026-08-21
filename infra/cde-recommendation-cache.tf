resource "aws_iam_role_policy" "application_task_cde_recommendation_cache" {
  name = "${local.name_prefix}-cde-recommendation-cache"
  role = aws_iam_role.application_task.id

  policy = module.data_plane.cde_cache_policy_json
}

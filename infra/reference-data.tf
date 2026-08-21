resource "aws_iam_role_policy" "application_task_reference_data" {
  name = "${local.name_prefix}-reference-data-read"
  role = aws_iam_role.application_task.id

  policy = module.data_plane.reference_policy_json
}

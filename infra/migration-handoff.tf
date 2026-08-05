removed {
  from = aws_iam_role.task_execution

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy_attachment.task_execution

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy.task_execution_secrets

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role.task

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy.task_workflow_storage

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role.codebuild

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_iam_role_policy.codebuild

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_ecs_task_definition.app

  lifecycle {
    destroy = false
  }
}

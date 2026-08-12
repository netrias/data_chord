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

# BDF state contains these two resources on the shared legacy Secrets Manager
# endpoint. An external client can still depend on the endpoint security group.
# Forget them during the one-time handoff, but never delete or detach them.
removed {
  from = aws_security_group.secrets_endpoint

  lifecycle {
    destroy = false
  }
}

removed {
  from = aws_vpc_endpoint_security_group_association.secretsmanager_tasks

  lifecycle {
    destroy = false
  }
}

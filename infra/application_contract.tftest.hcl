mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "084828580051"
      arn        = "arn:aws:sts::084828580051:assumed-role/datachord-deployer/test"
    }
  }

  mock_data "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws:secretsmanager:us-east-2:084828580051:secret:data-chord/test"
    }
  }

  mock_data "aws_iam_policy" {
    defaults = {
      policy = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::084828580051:role/application/data-chord-test"
    }
  }

  mock_resource "aws_lb" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-2:084828580051:loadbalancer/app/data-chord-test/0123456789abcdef"
      arn_suffix = "app/data-chord-test/0123456789abcdef"
      dns_name   = "data-chord-test.us-east-2.elb.amazonaws.com"
    }
  }

  mock_resource "aws_lb_target_group" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-2:084828580051:targetgroup/data-chord-test/0123456789abcdef"
      arn_suffix = "targetgroup/data-chord-test/0123456789abcdef"
    }
  }

  mock_resource "aws_cognito_user_pool" {
    defaults = {
      arn = "arn:aws:cognito-idp:us-east-2:084828580051:userpool/us-east-2_012345678"
    }
  }

  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-2:084828580051:data-chord-test"
    }
  }
}

variables {
  expected_account_id           = "084828580051"
  aws_region                    = "us-east-2"
  application_role_boundary_arn = "arn:aws:iam::084828580051:policy/datachord-application-role-boundary"
  application_role_path         = "/application/"

  environment                 = "qa"
  vpc_id                      = "vpc-0123456789abcdef0"
  public_subnet_ids           = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
  certificate_arn             = "arn:aws:acm:us-east-2:084828580051:certificate/00000000-0000-0000-0000-000000000000"
  domain_name                 = "data-chord.example.com"
  hosted_zone_name            = ""
  netrias_api_key_secret_name = "data-chord/qa/netrias-api-key"
  image_tag                   = "0123456789ab"
}

run "application_roles_follow_foundation_guardrails" {
  command = plan

  assert {
    condition = alltrue([
      aws_iam_role.application_task.path == var.application_role_path,
      aws_iam_role.application_task_execution.path == var.application_role_path,
      aws_iam_role.application_build.path == var.application_role_path,
    ])
    error_message = "All application roles must use the foundation IAM path."
  }

  assert {
    condition = alltrue([
      aws_iam_role.application_task.permissions_boundary == var.application_role_boundary_arn,
      aws_iam_role.application_task_execution.permissions_boundary == var.application_role_boundary_arn,
      aws_iam_role.application_build.permissions_boundary == var.application_role_boundary_arn,
    ])
    error_message = "All application roles must use the foundation permission boundary."
  }
}

run "application_role_names_are_owned_by_data_chord" {
  command = plan

  assert {
    condition = alltrue([
      aws_iam_role.application_task.name == "data-chord-qa-application-task",
      aws_iam_role.application_task_execution.name == "data-chord-qa-application-task-exec",
      aws_iam_role.application_build.name == "data-chord-qa-application-build",
    ])
    error_message = "Data Chord must define the final application role names."
  }
}

run "endpoint_resources_are_absent_without_shared_endpoint" {
  command = plan

  variables {
    secretsmanager_vpc_endpoint_id = ""
  }

  assert {
    condition = (
      length(aws_security_group.secrets_endpoint) == 0 &&
      length(aws_vpc_endpoint_security_group_association.secretsmanager_tasks) == 0
    )
    error_message = "Endpoint resources must be absent when the target has no shared endpoint."
  }
}

run "endpoint_resources_attach_when_shared_endpoint_exists" {
  command = plan

  variables {
    secretsmanager_vpc_endpoint_id = "vpce-0123456789abcdef0"
  }

  assert {
    condition = (
      length(aws_security_group.secrets_endpoint) == 1 &&
      length(aws_vpc_endpoint_security_group_association.secretsmanager_tasks) == 1
    )
    error_message = "Endpoint resources must attach when the target provides a shared endpoint."
  }
}

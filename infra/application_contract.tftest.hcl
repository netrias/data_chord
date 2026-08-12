mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "084828580051"
      arn        = "arn:aws:sts::084828580051:assumed-role/datachord-deployer/test"
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-2a", "us-east-2b", "us-east-2c"]
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

  mock_resource "aws_acm_certificate" {
    defaults = {
      arn = "arn:aws:acm:us-east-2:084828580051:certificate/00000000-0000-0000-0000-000000000001"
      domain_validation_options = [{
        domain_name           = "data-chord-qa.example.com"
        resource_record_name  = "_validation.data-chord-qa.example.com"
        resource_record_type  = "CNAME"
        resource_record_value = "_validation.acm-validations.aws"
      }]
    }
  }

  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-2:084828580051:data-chord-test"
    }
  }

  mock_resource "aws_ecs_service" {
    defaults = {
      arn = "arn:aws:ecs:us-east-2:084828580051:service/data-chord-qa/data-chord-qa"
    }
  }
}

variables {
  target_slug                   = "test"
  aws_partition                 = "aws"
  expected_account_id           = "084828580051"
  aws_region                    = "us-east-2"
  application_role_boundary_arn = "arn:aws:iam::084828580051:policy/datachord-application-role-boundary"
  application_role_path         = "/application/"

  environment                 = "qa"
  netrias_environment         = "staging"
  data_model_store_url        = "https://model.example.com"
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

run "external_certificate_tls_uses_supplied_hostname" {
  command = plan

  assert {
    condition = (
      length(aws_acm_certificate.app) == 0 &&
      length(aws_acm_certificate_validation.app) == 0 &&
      local.app_host == var.domain_name &&
      output.app_url == "https://${var.domain_name}"
    )
    error_message = "External-certificate TLS must use the supplied certificate and hostname."
  }
}

run "managed_tls_creates_certificate_for_hosted_zone" {
  command = plan

  variables {
    certificate_arn  = ""
    domain_name      = ""
    hosted_zone_name = "example.com"
    domain_label     = "data-chord-qa"
  }

  assert {
    condition = (
      length(aws_acm_certificate.app) == 1 &&
      length(aws_acm_certificate_validation.app) == 1 &&
      local.app_host == "data-chord-qa.example.com" &&
      output.app_url == "https://data-chord-qa.example.com"
    )
    error_message = "Managed TLS must create a certificate and hostname under the hosted zone."
  }
}

run "tls_rejects_missing_configuration" {
  command = plan

  variables {
    certificate_arn  = ""
    domain_name      = ""
    hosted_zone_name = ""
  }

  expect_failures = [var.certificate_arn]
}

run "tls_rejects_certificate_without_domain" {
  command = plan

  variables {
    certificate_arn  = "arn:aws:acm:us-east-2:084828580051:certificate/00000000-0000-0000-0000-000000000000"
    domain_name      = ""
    hosted_zone_name = ""
  }

  expect_failures = [var.certificate_arn]
}

run "tls_rejects_domain_without_certificate" {
  command = plan

  variables {
    certificate_arn  = ""
    domain_name      = "data-chord.example.com"
    hosted_zone_name = ""
  }

  expect_failures = [var.certificate_arn]
}

run "tls_rejects_mixed_external_and_managed_inputs" {
  command = plan

  variables {
    certificate_arn  = "arn:aws:acm:us-east-2:084828580051:certificate/00000000-0000-0000-0000-000000000000"
    domain_name      = "data-chord.example.com"
    hosted_zone_name = "example.com"
  }

  expect_failures = [var.certificate_arn]
}

run "ecs_event_rules_use_the_managed_service_identity" {
  command = plan

  assert {
    condition = alltrue([
      jsondecode(aws_cloudwatch_event_rule.ecs_service_error.event_pattern).resources == [aws_ecs_service.app.arn],
      jsondecode(aws_cloudwatch_event_rule.ecs_deployment_failed.event_pattern).resources == [aws_ecs_service.app.arn],
    ])
    error_message = "ECS event rules must use the managed service identity."
  }
}

run "codebuild_reads_the_public_repository_without_authentication" {
  command = plan

  assert {
    condition = (
      aws_codebuild_project.app_image.source[0].type == "GITHUB" &&
      aws_codebuild_project.app_image.source[0].location == "https://github.com/netrias/data_chord.git" &&
      length(aws_codebuild_project.app_image.source[0].auth) == 0
    )
    error_message = "CodeBuild must read the public Data Chord repository without account-specific source authentication."
  }
}

run "application_owns_its_network" {
  command = plan

  assert {
    condition = (
      aws_security_group.alb.vpc_id == aws_vpc.app.id &&
      aws_security_group.task.vpc_id == aws_vpc.app.id &&
      aws_lb.app.subnets == toset(aws_subnet.public[*].id) &&
      aws_ecs_service.app.network_configuration[0].subnets == toset(aws_subnet.public[*].id)
    )
    error_message = "Data Chord networking must use the VPC and public subnets owned by this stack."
  }

  assert {
    condition = (
      length(aws_subnet.public) == 2 &&
      alltrue([for subnet in aws_subnet.public : subnet.map_public_ip_on_launch])
    )
    error_message = "Data Chord must create two public application subnets."
  }
}

run "runtime_receives_the_foundation_data_model_store" {
  command = plan

  assert {
    condition = contains(
      jsondecode(aws_ecs_task_definition.application.container_definitions)[0].environment,
      {
        name  = "DATA_CHORD_NETRIAS_DATA_MODEL_STORE_URL"
        value = var.data_model_store_url
      }
    )
    error_message = "The application must receive the foundation data model store URL."
  }
}

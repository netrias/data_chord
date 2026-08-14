mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "084828580051"
      arn        = "arn:aws:sts::084828580051:assumed-role/datachord-deployer/test"
    }
  }

  mock_data "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws:secretsmanager:us-east-2:084828580051:secret:data-chord/qa/netrias-api-key"
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-2a", "us-east-2b", "us-east-2c"]
    }
  }

  mock_data "aws_route_table" {
    defaults = {
      id     = "rtb-0123456789abcdef0"
      vpc_id = "ryyXLP0q"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::084828580051:role/application/data-chord-qa"
    }
  }

  mock_resource "aws_lb" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-2:084828580051:loadbalancer/app/data-chord-qa/0123456789abcdef"
      arn_suffix = "app/data-chord-qa/0123456789abcdef"
      dns_name   = "data-chord-qa.us-east-2.elb.amazonaws.com"
    }
  }

  mock_resource "aws_lb_target_group" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-2:084828580051:targetgroup/data-chord-qa/0123456789abcdef"
      arn_suffix = "targetgroup/data-chord-qa/0123456789abcdef"
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

  mock_resource "aws_ecs_service" {
    defaults = {
      arn = "arn:aws:ecs:us-east-2:084828580051:service/data-chord-qa/data-chord-qa"
    }
  }
}

variables {
  expected_account_id           = "084828580051"
  aws_region                    = "us-east-2"
  application_role_boundary_arn = "arn:aws:iam::084828580051:policy/datachord-application-role-boundary"
  application_role_path         = "/application/"

  environment       = "qa"
  deployment_target = "bdf"
  hosted_zone_name  = "example.com"
  domain_label      = "data-chord-qa"
  image_tag         = "0123456789ab"
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

run "managed_https_entrypoint_uses_the_hosted_zone" {
  command = plan

  assert {
    condition = (
      aws_acm_certificate.app.domain_name == "data-chord-qa.example.com" &&
      aws_acm_certificate_validation.app.certificate_arn == aws_acm_certificate.app.arn &&
      output.app_url == "https://data-chord-qa.example.com"
    )
    error_message = "The app must create and validate its HTTPS hostname in the configured hosted zone."
  }
}

run "public_entrypoint_requires_cognito_authentication" {
  command = plan

  assert {
    condition = (
      alltrue([
        for rule in aws_security_group.alb.ingress :
        rule.from_port == 443 && rule.to_port == 443
      ]) &&
      [for action in aws_lb_listener.https.default_action : action.type] == ["authenticate-cognito", "forward"]
    )
    error_message = "The public entrypoint must accept only HTTPS and authenticate with Cognito before forwarding."
  }
}

run "reference_data_has_a_dedicated_durable_table" {
  command = plan

  assert {
    condition = (
      aws_dynamodb_table.reference_data.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.reference_data.hash_key == "pk" &&
      aws_dynamodb_table.reference_data.range_key == "sk" &&
      length(aws_dynamodb_table.reference_data.global_secondary_index) == 0 &&
      toset(jsondecode(aws_iam_role_policy.application_task_reference_data.policy).Statement[0].Action) == toset([
        "dynamodb:DescribeTable",
        "dynamodb:Query",
      ]) &&
      length(aws_iam_role.reference_data_importer) == 0 &&
      output.reference_data_table == aws_dynamodb_table.reference_data.name
    )
    error_message = "Reference data must use the dedicated table, read-only runtime access, and no permanent importer."
  }
}

run "ecs_service_output_uses_the_managed_service_identity" {
  command = plan

  assert {
    condition     = output.ecs_service_name == aws_ecs_service.app.name
    error_message = "The ECS service output must use the managed service identity."
  }
}

run "codebuild_uses_public_source_and_read_only_dependency_credential" {
  command = plan

  assert {
    condition = (
      aws_codebuild_project.app_image.source[0].type == "GITHUB" &&
      aws_codebuild_project.app_image.source[0].location == "https://github.com/netrias/data_chord.git" &&
      length(aws_codebuild_project.app_image.source[0].auth) == 0
    )
    error_message = "CodeBuild must read the public Data Chord repository without source authentication."
  }

  assert {
    condition = (
      data.aws_secretsmanager_secret.github_app.name == "data-chord/build/github-app" &&
      anytrue([
        for statement in jsondecode(aws_iam_role_policy.application_build.policy).Statement :
        statement.Resource == data.aws_secretsmanager_secret.github_app.arn
      ])
    )
    error_message = "CodeBuild must read only the configured GitHub App credential."
  }
}

run "agentic_harmonization_is_the_only_scaled_runtime" {
  command = plan

  assert {
    condition = (
      aws_ecs_task_definition.application.cpu == tostring(4096) &&
      aws_ecs_task_definition.application.memory == tostring(8192)
    )
    error_message = "Agentic harmonization must default to the larger task shape."
  }

  # Given agentic harmonization uses AWS short-term bearer tokens and the default project,
  # when the ECS task policy is planned,
  # then it permits only token use and inference on that project.
  assert {
    condition = alltrue([
      anytrue([
        for statement in jsondecode(aws_iam_role_policy.application_task_bedrock_mantle.policy).Statement :
        toset(statement.Action) == toset([
          "bedrock:CallWithBearerToken",
          "bedrock-mantle:CallWithBearerToken",
        ]) && statement.Resource == "*"
      ]),
      anytrue([
        for statement in jsondecode(aws_iam_role_policy.application_task_bedrock_mantle.policy).Statement :
        toset(statement.Action) == toset(["bedrock-mantle:CreateInference"]) &&
        statement.Resource == "arn:aws:bedrock-mantle:us-east-2:084828580051:project/default"
      ]),
    ])
    error_message = "The task role must allow bearer-token use and inference only on the account's default Bedrock project."
  }
}

run "application_owns_a_minimal_public_network" {
  command = plan

  assert {
    condition = (
      aws_vpc.app.cidr_block == "10.0.0.0/16" &&
      aws_vpc.app.enable_dns_support &&
      aws_vpc.app.enable_dns_hostnames &&
      length(aws_subnet.public) == 2 &&
      alltrue([
        for subnet in aws_subnet.public :
        subnet.vpc_id == aws_vpc.app.id && subnet.map_public_ip_on_launch
      ]) &&
      length(distinct([for subnet in aws_subnet.public : subnet.availability_zone])) == 2
    )
    error_message = "The application must own two public subnets in separate availability zones."
  }

  assert {
    condition = (
      aws_internet_gateway.app.vpc_id == aws_vpc.app.id &&
      data.aws_route_table.main.vpc_id == aws_vpc.app.id &&
      aws_route.public_internet.destination_cidr_block == "0.0.0.0/0" &&
      aws_route.public_internet.gateway_id == aws_internet_gateway.app.id
    )
    error_message = "The VPC main route table must use the application internet gateway."
  }
}

run "workflow_storage_uses_the_canonical_deployment_prefix" {
  command = plan

  assert {
    condition = (
      local.deployment_prefix == "datachord/bdf/qa" &&
      anytrue([
        for environment_variable in jsondecode(aws_ecs_task_definition.application.container_definitions)[0].environment :
        environment_variable.name == "DATA_CHORD_S3_PREFIX" && environment_variable.value == local.deployment_prefix
      ]) &&
      anytrue([
        for statement in jsondecode(aws_iam_role_policy.application_task_workflow_storage.policy).Statement :
        try(statement.Resource, "") == "${aws_s3_bucket.workflow.arn}/${local.deployment_prefix}/*"
      ])
    )
    error_message = "Workflow storage must use datachord/<target>/<stage>."
  }
}

run "runtime_and_entrypoint_use_the_application_network" {
  command = plan

  assert {
    condition = (
      aws_security_group.alb.vpc_id == aws_vpc.app.id &&
      aws_security_group.task.vpc_id == aws_vpc.app.id &&
      aws_lb_target_group.app.vpc_id == aws_vpc.app.id &&
      toset(aws_lb.app.subnets) == toset(local.public_subnet_ids) &&
      toset(aws_ecs_service.app.network_configuration[0].subnets) == toset(local.public_subnet_ids) &&
      aws_ecs_service.app.network_configuration[0].assign_public_ip
    )
    error_message = "The ALB and Fargate task must use the application-owned public network."
  }
}

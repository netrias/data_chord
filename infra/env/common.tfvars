project_name = "data-chord"
aws_region   = "us-east-2"

vpc_id = "vpc-0fce4109b9575d124"
public_subnet_ids = [
  "subnet-0115982c04fa2e62a",
  "subnet-06a392e3c0aed038e",
  "subnet-046ea5c040e6fd135",
]
secretsmanager_vpc_endpoint_id = "vpce-09e34dd0835d4c005"

certificate_arn  = ""
domain_name      = ""
hosted_zone_name = "netriasbdf.cloud"

cognito_domain_prefix = ""
desired_count         = 1

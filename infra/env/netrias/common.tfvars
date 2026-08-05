project_name = "data-chord"

vpc_id = "vpc-08c111f13ad3e8b44"
public_subnet_ids = [
  "subnet-048dc428f9192baff",
  "subnet-0d468895242300491",
  "subnet-0aa3315367c653ff2",
]
secretsmanager_vpc_endpoint_id = ""

certificate_arn  = ""
domain_name      = ""
hosted_zone_name = "apps.netrias.com"

cognito_domain_prefix = ""
desired_count         = 1

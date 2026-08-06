project_name = "data-chord"

vpc_id = "vpc-08c111f13ad3e8b44"
public_subnet_ids = [
  "subnet-048dc758402e95744",
  "subnet-0d468bc4f14a6ac33",
  "subnet-0aa3311feda432c84",
]
secretsmanager_vpc_endpoint_id = ""

certificate_arn  = ""
domain_name      = ""
hosted_zone_name = "apps.netrias.com"

codebuild_connection_arn = "arn:aws:codeconnections:us-east-2:945365518758:connection/bb057081-ca91-46eb-b943-d8d2738a577c"

cognito_domain_prefix = ""
desired_count         = 1

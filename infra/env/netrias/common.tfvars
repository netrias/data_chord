project_name   = "data-chord"
aws_region     = "us-east-2"
aws_account_id = "945365518758"

vpc_id = "vpc-08c111f13ad3e8b44"
public_subnet_ids = [
  "subnet-048dc758402e95744",
  "subnet-0d468bc4f14a6ac33",
  "subnet-0aa3311feda432c84",
]

# The zone must be created in Route 53 and delegated from netrias.com before
# the first deployment so ACM can validate the generated certificates.
certificate_arn  = ""
domain_name      = ""
hosted_zone_name = "apps.netrias.com"

cognito_domain_prefix = ""
desired_count         = 1

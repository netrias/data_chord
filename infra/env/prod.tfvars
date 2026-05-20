project_name = "data-chord"
environment  = "prod"
aws_region   = "us-east-2"

vpc_id = "vpc-0fce4109b9575d124"
public_subnet_ids = [
  "subnet-0115982c04fa2e62a",
  "subnet-06a392e3c0aed038e",
  "subnet-046ea5c040e6fd135",
]

certificate_arn  = ""
domain_name      = ""
hosted_zone_name = "netriasbdf.cloud"
domain_label     = "netrias-data-chord"

netrias_api_key_secret_name = "data-chord/prod/netrias-api-key"

cognito_domain_prefix = ""
desired_count         = 1

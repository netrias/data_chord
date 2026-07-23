# bucket and region are resolved at deploy time from the active AWS account
# and this environment's aws_region (see infra/scripts/lib.sh resolve_state_bucket_name).
key          = "data-chord/prod/tofu.tfstate"
encrypt      = true
use_lockfile = true

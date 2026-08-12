domain_label     = "netrias-data-chord"
hosted_zone_name = "netriasbdf.cloud"

netrias_environment = "prod"

alert_email_addresses = ["charman@netrias.com"]

target_group_deregistration_delay_seconds  = 300
target_group_health_check_interval_seconds = 30
target_group_healthy_threshold             = 2
target_group_unhealthy_threshold           = 3

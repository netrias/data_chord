data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  vpc_id = var.existing_network == null ? aws_vpc.app[0].id : data.aws_vpc.existing[0].id
  public_subnet_cidrs = {
    for index, availability_zone in slice(data.aws_availability_zones.available.names, 0, 2) :
    availability_zone => cidrsubnet("10.0.0.0/16", 8, index)
  }
  public_subnet_ids = var.existing_network == null ? [
    for availability_zone in sort(keys(local.public_subnet_cidrs)) :
    aws_subnet.public[availability_zone].id
  ] : sort(tolist(var.existing_network.public_subnet_ids))
}

data "aws_vpc" "existing" {
  count = var.existing_network == null ? 0 : 1
  id    = var.existing_network.vpc_id
}

data "aws_subnet" "existing" {
  for_each = var.existing_network == null ? toset([]) : var.existing_network.public_subnet_ids
  id       = each.value

  lifecycle {
    postcondition {
      condition     = self.vpc_id == local.vpc_id
      error_message = "Every supplied subnet must belong to the existing VPC."
    }
  }
}

moved {
  from = aws_vpc.app
  to   = aws_vpc.app[0]
}

moved {
  from = aws_internet_gateway.app
  to   = aws_internet_gateway.app[0]
}

moved {
  from = aws_route.public_internet
  to   = aws_route.public_internet[0]
}

resource "aws_vpc" "app" {
  count                = var.existing_network == null ? 1 : 0
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, {
    Name = local.name_prefix
  })
}

resource "aws_internet_gateway" "app" {
  count  = var.existing_network == null ? 1 : 0
  vpc_id = aws_vpc.app[0].id

  tags = merge(local.common_tags, {
    Name = local.name_prefix
  })
}

resource "aws_subnet" "public" {
  for_each = var.existing_network == null ? local.public_subnet_cidrs : {}

  vpc_id                  = aws_vpc.app[0].id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-public-${each.key}"
  })
}

data "aws_route_table" "main" {
  count  = var.existing_network == null ? 1 : 0
  vpc_id = aws_vpc.app[0].id

  filter {
    name   = "association.main"
    values = ["true"]
  }
}

resource "aws_route" "public_internet" {
  count                  = var.existing_network == null ? 1 : 0
  route_table_id         = data.aws_route_table.main[0].id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.app[0].id
}

moved {
  from = aws_security_group.secrets_endpoint
  to   = aws_security_group.secrets_endpoint[0]
}

moved {
  from = aws_vpc_endpoint_security_group_association.secretsmanager_tasks
  to   = aws_vpc_endpoint_security_group_association.secretsmanager_tasks[0]
}

resource "aws_security_group" "secrets_endpoint" {
  count       = var.existing_network == null ? 0 : 1
  name        = "${local.name_prefix}-secrets-endpoint"
  description = "Secrets Manager VPC endpoint access from Data Chord tasks"
  vpc_id      = local.vpc_id

  ingress {
    description     = "Secrets Manager HTTPS from app tasks"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.task.id]
  }

  dynamic "ingress" {
    for_each = var.existing_network == null ? toset([]) : var.existing_network.additional_secretsmanager_client_security_group_ids
    content {
      description     = "Secrets Manager HTTPS from additional client"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      security_groups = [ingress.value]
    }
  }

  egress {
    description = "Outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.common_tags
}

resource "aws_vpc_endpoint_security_group_association" "secretsmanager_tasks" {
  count             = var.existing_network == null ? 0 : 1
  vpc_endpoint_id   = var.existing_network.secretsmanager_endpoint_id
  security_group_id = aws_security_group.secrets_endpoint[0].id
}

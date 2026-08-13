data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  public_subnet_cidrs = {
    for index, availability_zone in slice(data.aws_availability_zones.available.names, 0, 2) :
    availability_zone => cidrsubnet("10.0.0.0/16", 8, index)
  }
  public_subnet_ids = [
    for availability_zone in sort(keys(local.public_subnet_cidrs)) :
    aws_subnet.public[availability_zone].id
  ]
}

resource "aws_vpc" "app" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, {
    Name = local.name_prefix
  })
}

resource "aws_internet_gateway" "app" {
  vpc_id = aws_vpc.app.id

  tags = merge(local.common_tags, {
    Name = local.name_prefix
  })
}

resource "aws_subnet" "public" {
  for_each = local.public_subnet_cidrs

  vpc_id                  = aws_vpc.app.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-public-${each.key}"
  })
}

data "aws_route_table" "main" {
  vpc_id = aws_vpc.app.id

  filter {
    name   = "association.main"
    values = ["true"]
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = data.aws_route_table.main.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.app.id
}

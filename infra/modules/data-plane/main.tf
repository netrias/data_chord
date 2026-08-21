resource "aws_s3_bucket" "workflow" {
  bucket = "${var.name_prefix}-workflow-${var.account_id}-${var.aws_region}"

  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-workflow"
  })
}

resource "aws_s3_bucket_public_access_block" "workflow" {
  bucket = aws_s3_bucket.workflow.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "reference_data" {
  name         = "${var.name_prefix}-reference-data"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-reference-data"
  })
}

resource "aws_dynamodb_table" "harmonization_cache" {
  name         = "${var.name_prefix}-harmonization-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "cache_key"

  attribute {
    name = "cache_key"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = true

  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-harmonization-cache"
  })
}

resource "aws_dynamodb_table" "cde_recommendation_cache" {
  name         = "${var.name_prefix}-cde-recommendation-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "cache_key"

  attribute {
    name = "cache_key"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = merge(var.common_tags, {
    Name = "${var.name_prefix}-cde-recommendation-cache"
  })
}

locals {
  workflow_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.workflow.arn}/${var.deployment_prefix}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.workflow.arn
        Condition = {
          StringLike = {
            "s3:prefix" = ["${var.deployment_prefix}/*"]
          }
        }
      }
    ]
  }

  reference_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:DescribeTable", "dynamodb:Query"]
      Resource = aws_dynamodb_table.reference_data.arn
    }]
  }

  harmonization_cache_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:DescribeTable", "dynamodb:GetItem", "dynamodb:PutItem"]
      Resource = aws_dynamodb_table.harmonization_cache.arn
    }]
  }

  cde_cache_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:BatchGetItem", "dynamodb:BatchWriteItem", "dynamodb:DescribeTable"]
      Resource = aws_dynamodb_table.cde_recommendation_cache.arn
    }]
  }

  bedrock_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["bedrock:CallWithBearerToken", "bedrock-mantle:CallWithBearerToken"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-mantle:CreateInference"]
        Resource = "arn:${var.partition}:bedrock-mantle:${var.aws_region}:${var.account_id}:project/default"
      }
    ]
  }

  reference_loader_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query",
      ]
      Resource = aws_dynamodb_table.reference_data.arn
    }]
  }
}

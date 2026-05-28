# AWS provider pointed at LocalStack.
#
# The `endpoints` block redirects every service call to localhost:4566 where
# LocalStack listens. Without this block, Terraform would try to reach real
# AWS endpoints (s3.amazonaws.com, etc.) and either fail with bad credentials
# or — worse — provision real billable resources.
#
# `skip_credentials_validation` and friends are required because LocalStack
# accepts dummy credentials and STS validation would otherwise fail.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  access_key                  = "test"
  secret_key                  = "test"
  region                      = var.aws_region
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3       = var.localstack_endpoint
    lambda   = var.localstack_endpoint
    dynamodb = var.localstack_endpoint
    iam      = var.localstack_endpoint
    sts      = var.localstack_endpoint
    logs     = var.localstack_endpoint
  }
}

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state file (terraform.tfstate, gitignored) — deliberate, not an
  # oversight. No S3+DynamoDB remote backend: this deployment is meant to
  # be applied and destroyed within a single demo session on one machine,
  # not shared/persisted across machines or sessions — a remote backend
  # would itself be the one resource in this stack that runs 24/7, which
  # is exactly what everything else here is designed to avoid. See
  # DECISIONS.md D33.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
    }
  }
}

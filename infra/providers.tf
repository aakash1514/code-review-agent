provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "code-review-agent"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = var.region

  # The dashboard, API, WebSocket and webhook all live behind one CloudFront
  # domain. CloudFront depends on the ALB, the task on CloudFront: no cycle.
  public_base_url = "https://${aws_cloudfront_distribution.this.domain_name}"
}

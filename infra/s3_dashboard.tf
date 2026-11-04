# The React dashboard build (deploy-web.yml syncs it here). Private: only this
# account's CloudFront distribution can read it, through OAC (cloudfront.tf).

#trivy:ignore:AVD-AWS-0089 No access logging: public static assets, CloudFront is the only reader
#trivy:ignore:AVD-AWS-0090 No versioning: every object is rebuilt from git by deploy-web.yml
resource "aws_s3_bucket" "dashboard" {
  bucket        = "cra-dashboard-${local.account_id}"
  force_destroy = true # destroyable on purpose; the content is a build artifact
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

#trivy:ignore:AVD-AWS-0132 SSE-S3, not a KMS CMK: the content is the public dashboard build, and OAC reads of SSE-KMS objects need an extra key policy
resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled       = true
    blocked_encryption_types = ["SSE-C"]
  }
}

resource "aws_s3_bucket_policy" "dashboard" {
  bucket = aws_s3_bucket.dashboard.id
  policy = data.aws_iam_policy_document.dashboard_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.dashboard]
}

data "aws_iam_policy_document" "dashboard_bucket" {
  statement {
    sid       = "AllowCloudFrontServicePrincipal"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.dashboard.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    # The cloudfront.amazonaws.com principal is shared by every distribution
    # in every account; this condition narrows it to ours.
    condition {
      test     = "ArnLike"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.this.arn]
    }
  }
}

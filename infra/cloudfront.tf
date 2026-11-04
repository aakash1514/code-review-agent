# One distribution, one domain: the dashboard from S3, everything dynamic from
# the ALB. Same origin for the SPA and the API, so no CORS.

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

# Forwards every viewer header, cookie and query string, except Host (the ALB
# expects its own name). WebSocket upgrades need the forwarded headers.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

# Origin Access Control: CloudFront signs its S3 requests with SigV4, so the
# bucket can stay private (s3_dashboard.tf). Replaces the legacy OAI.
resource "aws_cloudfront_origin_access_control" "dashboard" {
  name                              = "cra-dashboard-oac"
  description                       = "CloudFront -> cra-dashboard bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

locals {
  alb_origin_id = "alb"
  s3_origin_id  = "s3-dashboard"
  # Routed to the ALB; anything else is a dashboard file from S3.
  alb_paths = ["/api/*", "/auth/*", "/ws/*", "/webhook", "/healthz"]
}

#trivy:ignore:AVD-AWS-0011 No WAF: ~$6/month minimum; the app authenticates every route that does work (HMAC webhook, GitHub OAuth)
resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  is_ipv6_enabled     = true
  http_version        = "http2"
  price_class         = "PriceClass_200"
  default_root_object = "index.html"
  comment             = ""

  origin {
    origin_id                = local.s3_origin_id
    domain_name              = aws_s3_bucket.dashboard.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard.id
    connection_attempts      = 3
    connection_timeout       = 10
  }

  origin {
    origin_id           = local.alb_origin_id
    domain_name         = aws_lb.this.dns_name
    origin_path         = ""
    connection_attempts = 3
    connection_timeout  = 10

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"] # required by the schema; unused with http-only
      origin_read_timeout      = 30
      origin_keepalive_timeout = 5
      ip_address_type          = "ipv4"
    }

    # The app rejects any request without this header (except /healthz), so
    # the ALB is useless to anyone who finds its DNS name.
    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify.result
    }
  }

  default_cache_behavior {
    target_origin_id       = local.s3_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
  }

  dynamic "ordered_cache_behavior" {
    for_each = local.alb_paths

    content {
      path_pattern             = ordered_cache_behavior.value
      target_origin_id         = local.alb_origin_id
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods           = ["GET", "HEAD"]
      compress                 = true
      cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
      origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    }
  }

  # No custom error responses, as live: the dashboard has no client-side
  # routes, so nothing needs an index.html fallback.

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

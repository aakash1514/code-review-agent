# Logs, alarms -> SNS email, the CloudWatch dashboard and saved Logs Insights
# queries. App metrics are EMF under the CodeReviewAgent namespace.

#trivy:ignore:AVD-AWS-0017 Default CloudWatch Logs encryption: a CMK costs $1/month, and secrets never reach the logs
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/code-review-agent"
  retention_in_days = 14
}

# Unencrypted, as live: CloudWatch alarms can't publish to a topic encrypted
# with the AWS-managed aws/sns key, and the messages are alarm metadata only.
#trivy:ignore:AVD-AWS-0095 CloudWatch alarms can't publish to an aws/sns-encrypted topic; a CMK isn't worth $1/month for alarm metadata
resource "aws_sns_topic" "alerts" {
  name = "cra-alerts"
}

# Stays "pending confirmation" until the link in the email is clicked.
resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---- alarms -----------------------------------------------------------------

locals {
  app_metric_dims = { Service = "code-review-agent" }
  alb_dims        = { LoadBalancer = aws_lb.this.arn_suffix }
  ecs_dims        = { ClusterName = aws_ecs_cluster.this.name, ServiceName = aws_ecs_service.app.name }

  alarms = {
    "cra-alb-target-5xx" = {
      description = "App returned 5xx through the ALB"
      namespace   = "AWS/ApplicationELB"
      metric      = "HTTPCode_Target_5XX_Count"
      statistic   = "Sum"
      dimensions  = local.alb_dims
      period      = 300
      periods     = 1
      threshold   = 0
      comparison  = "GreaterThanThreshold"
      missing     = "notBreaching"
    }
    "cra-bedrock-failover" = {
      description = "LLM calls failing over from Bedrock to Groq (check IAM/quota)"
      namespace   = "CodeReviewAgent"
      metric      = "BedrockProviderFailures"
      statistic   = "Sum"
      dimensions  = {}
      period      = 3600
      periods     = 1
      threshold   = 0
      comparison  = "GreaterThanThreshold"
      missing     = "notBreaching"
    }
    "cra-ecs-memory-high" = {
      description = "Task memory above 85% for 10 minutes"
      namespace   = "AWS/ECS"
      metric      = "MemoryUtilization"
      statistic   = "Average"
      dimensions  = local.ecs_dims
      period      = 300
      periods     = 2
      threshold   = 85
      comparison  = "GreaterThanThreshold"
      missing     = "notBreaching"
    }
    "cra-guardrail-errors" = {
      description = "Guardrail screen failing (fail-open): check IAM/region"
      namespace   = "CodeReviewAgent"
      metric      = "GuardrailErrors"
      statistic   = "Sum"
      dimensions  = local.app_metric_dims
      period      = 3600
      periods     = 1
      threshold   = 0
      comparison  = "GreaterThanThreshold"
      missing     = "notBreaching"
    }
    "cra-no-healthy-target" = {
      description = "No healthy ECS task behind the ALB for 5 minutes"
      namespace   = "AWS/ApplicationELB"
      metric      = "HealthyHostCount"
      statistic   = "Maximum"
      dimensions  = merge(local.alb_dims, { TargetGroup = aws_lb_target_group.app.arn_suffix })
      period      = 60
      periods     = 5
      threshold   = 1
      comparison  = "LessThanThreshold"
      missing     = "breaching" # no data at all = no task
    }
    "cra-review-p95-slow" = {
      description        = "p95 review duration above 120 s"
      namespace          = "CodeReviewAgent"
      metric             = "ReviewDurationMs"
      extended_statistic = "p95"
      dimensions         = local.app_metric_dims
      period             = 900
      periods            = 1
      threshold          = 120000
      comparison         = "GreaterThanThreshold"
      missing            = "notBreaching"
    }
    "cra-reviews-failed" = {
      description = "2+ failed reviews in an hour"
      namespace   = "CodeReviewAgent"
      metric      = "ReviewsFailed"
      statistic   = "Sum"
      dimensions  = local.app_metric_dims
      period      = 3600
      periods     = 1
      threshold   = 2
      comparison  = "GreaterThanOrEqualToThreshold"
      missing     = "notBreaching"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each = local.alarms

  alarm_name          = each.key
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = try(each.value.statistic, null)
  extended_statistic  = try(each.value.extended_statistic, null)
  dimensions          = each.value.dimensions
  period              = each.value.period
  evaluation_periods  = each.value.periods
  threshold           = each.value.threshold
  comparison_operator = each.value.comparison
  treat_missing_data  = each.value.missing

  actions_enabled = true
  alarm_actions   = [aws_sns_topic.alerts.arn]
  ok_actions      = [aws_sns_topic.alerts.arn]
}

# ---- dashboard ----------------------------------------------------------------

locals {
  alb_suffix = aws_lb.this.arn_suffix
  tg_suffix  = aws_lb_target_group.app.arn_suffix
  cluster    = aws_ecs_cluster.this.name
  service    = aws_ecs_service.app.name
}

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = "code-review-agent"

  dashboard_body = jsonencode({
    widgets = [
      {
        type       = "text", x = 0, y = 0, width = 24, height = 1
        properties = { markdown = "## Code Review Agent: reviews, LLM usage, gate, infrastructure" }
      },
      {
        type = "metric", x = 0, y = 1, width = 8, height = 6
        properties = {
          title = "Reviews per hour", region = local.region, view = "timeSeries", stat = "Sum", period = 3600
          metrics = [
            ["CodeReviewAgent", "ReviewsCompleted", "Service", "code-review-agent", { label = "completed" }],
            [".", "ReviewsFailed", ".", ".", { label = "failed", color = "#d62728" }],
          ]
        }
      },
      {
        type = "metric", x = 8, y = 1, width = 8, height = 6
        properties = {
          title = "Review duration (ms)", region = local.region, view = "timeSeries", period = 3600
          metrics = [
            ["CodeReviewAgent", "ReviewDurationMs", "Service", "code-review-agent", { stat = "p50", label = "p50" }],
            ["...", { stat = "p95", label = "p95" }],
          ]
          annotations = { horizontal = [{ label = "p95 alarm", value = 120000 }] }
        }
      },
      {
        type = "metric", x = 16, y = 1, width = 8, height = 6
        properties = {
          title = "LLM tokens and calls per review (avg)", region = local.region, view = "timeSeries", stat = "Average", period = 3600
          metrics = [
            ["CodeReviewAgent", "LLMTokens", "Service", "code-review-agent", { label = "tokens" }],
            [".", "LLMCalls", ".", ".", { label = "calls", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 7, width = 8, height = 6
        properties = {
          title = "Groq pressure: rate-limit wait (ms) and fallbacks", region = local.region, view = "timeSeries", stat = "Sum", period = 3600
          metrics = [
            ["CodeReviewAgent", "RateLimitWaitMs", "Service", "code-review-agent", { label = "429 wait ms" }],
            [".", "LLMFallbacks", ".", ".", { label = "fallbacks", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 8, y = 7, width = 8, height = 6
        properties = {
          title = "Merge gate", region = local.region, view = "timeSeries", stat = "Sum", period = 3600
          metrics = [
            ["CodeReviewAgent", "CriticalFindings", "Service", "code-review-agent", { label = "critical findings" }],
            [".", "MergeBlocked", ".", ".", { label = "merges blocked" }],
          ]
        }
      },
      {
        type = "alarm", x = 16, y = 7, width = 8, height = 6
        properties = {
          title = "Alarms"
          alarms = [for name in [
            "cra-alb-target-5xx",
            "cra-no-healthy-target",
            "cra-reviews-failed",
            "cra-ecs-memory-high",
            "cra-review-p95-slow",
          ] : aws_cloudwatch_metric_alarm.this[name].arn]
        }
      },
      {
        type = "metric", x = 0, y = 13, width = 8, height = 6
        properties = {
          title = "ALB requests and target 5xx", region = local.region, view = "timeSeries", stat = "Sum", period = 300
          metrics = [
            ["AWS/ApplicationELB", "RequestCount", "LoadBalancer", local.alb_suffix, { label = "requests" }],
            [".", "HTTPCode_Target_5XX_Count", ".", ".", { label = "target 5xx", color = "#d62728", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 8, y = 13, width = 8, height = 6
        properties = {
          title = "Healthy tasks behind the ALB", region = local.region, view = "timeSeries", stat = "Minimum", period = 60
          metrics = [
            ["AWS/ApplicationELB", "HealthyHostCount", "LoadBalancer", local.alb_suffix, "TargetGroup", local.tg_suffix],
          ]
        }
      },
      {
        type = "metric", x = 16, y = 13, width = 8, height = 6
        properties = {
          title = "ECS task CPU and memory (%)", region = local.region, view = "timeSeries", stat = "Average", period = 300
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", local.cluster, "ServiceName", local.service, { label = "CPU" }],
            [".", "MemoryUtilization", ".", ".", ".", ".", { label = "memory" }],
          ]
          yAxis = { left = { min = 0, max = 100 } }
        }
      },
      {
        type = "log", x = 0, y = 19, width = 24, height = 6
        properties = {
          title = "Latest reviews", region = local.region, view = "table"
          query = "SOURCE '${aws_cloudwatch_log_group.app.name}' | fields @timestamp, repo, pr, commit, status, duration_ms, llm_calls, LLMTokens, CriticalFindings, MergeBlocked | filter event = \"review_finished\" | sort @timestamp desc | limit 20"
        }
      },
    ]
  })
}

# ---- saved Logs Insights queries ------------------------------------------------

locals {
  saved_queries = {
    "cra/reindex-runs"            = "filter event = \"reindex_finished\" | fields @timestamp, repo, commit, status, chunks, duration_ms, error | sort @timestamp desc"
    "cra/reviews-by-status"       = "filter event = \"review_finished\" | stats count(*) as reviews by status"
    "cra/groq-429s-per-hour"      = "filter msg like /\\[groq\\] 429 from/ | parse msg /429 from (?<model>[^:]+):/ | stats count(*) as rate_limited by model, bin(1h)"
    "cra/llm-failures-by-model"   = "filter msg like /\\[model_client\\]/ | parse msg /\\[model_client\\] (?<tier>\\w+) \\((?<model>[^)]+)\\) failed for task=(?<task>\\w+): (?<error>\\w+)/ | stats count(*) as failures by model, task, error"
    "cra/review-duration-p50-p95" = "filter event = \"review_finished\" and status = \"complete\" | stats pct(duration_ms, 50) as p50_ms, pct(duration_ms, 95) as p95_ms, avg(LLMTokens) as avg_tokens, count(*) as reviews by bin(1h)"
  }
}

resource "aws_cloudwatch_query_definition" "this" {
  for_each = local.saved_queries

  name            = each.key
  log_group_names = [aws_cloudwatch_log_group.app.name]
  query_string    = each.value
}

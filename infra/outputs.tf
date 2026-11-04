# The GitHub environment variables each output feeds are in README.md.

output "public_base_url" {
  description = "Dashboard, API and webhook base URL (GitHub var PUBLIC_BASE_URL)."
  value       = local.public_base_url
}

output "github_webhook_url" {
  description = "GitHub App webhook URL."
  value       = "${local.public_base_url}/webhook"
}

output "github_oauth_callback_url" {
  description = "GitHub App callback URL."
  value       = "${local.public_base_url}/auth/callback"
}

output "cloudfront_distribution_id" {
  description = "GitHub var CF_DISTRIBUTION_ID."
  value       = aws_cloudfront_distribution.this.id
}

output "dashboard_bucket" {
  description = "GitHub var S3_BUCKET."
  value       = aws_s3_bucket.dashboard.bucket
}

output "ecr_repository_name" {
  description = "GitHub var ECR_REPOSITORY."
  value       = aws_ecr_repository.app.name
}

output "ecr_repository_url" {
  description = "For a manual docker push."
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  description = "GitHub var ECS_CLUSTER."
  value       = aws_ecs_cluster.this.name
}

output "ecs_service_name" {
  description = "GitHub var ECS_SERVICE."
  value       = aws_ecs_service.app.name
}

output "ecs_task_family" {
  description = "GitHub var ECS_TASK_FAMILY."
  value       = aws_ecs_task_definition.app.family
}

output "container_name" {
  description = "GitHub var CONTAINER_NAME."
  value       = local.container_name
}

output "deploy_role_arn" {
  description = "GitHub var AWS_ROLE_ARN (environment production)."
  value       = aws_iam_role.gha_deploy.arn
}

output "evals_role_arn" {
  description = "GitHub var AWS_EVALS_ROLE_ARN (environment evals). Managed outside Terraform."
  value       = data.aws_iam_role.gha_evals.arn
}

output "app_secret_arn" {
  description = "Put the app secret values here after the first apply."
  value       = aws_secretsmanager_secret.app.arn
}

output "alb_dns_name" {
  description = "Direct ALB name (only CloudFront can reach it)."
  value       = aws_lb.this.dns_name
}

output "alerts_topic_arn" {
  description = "SNS topic the alarms publish to."
  value       = aws_sns_topic.alerts.arn
}

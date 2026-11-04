#trivy:ignore:AVD-AWS-0034 Container Insights off, as live: ~$0.50/day for one task; EMF metrics and the CloudWatch dashboard cover it
resource "aws_ecs_cluster" "this" {
  name = "cra-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

locals {
  container_name = "app"

  # Non-secret settings only. Secrets come from Secrets Manager (below).
  app_environment = {
    AWS_REGION                = local.region
    BEDROCK_EMBED_DIM         = "512"
    BEDROCK_EMBED_REGION      = local.region
    DASHBOARD_BASE_URL        = local.public_base_url
    DATA_DIR                  = "/data"
    EMBEDDING_PROVIDER        = "bedrock"
    GITHUB_APP_ID             = var.github_app_id
    GITHUB_INSTALLATION_ID    = var.github_installation_id
    GITHUB_OAUTH_CLIENT_ID    = var.github_oauth_client_id
    GITHUB_OAUTH_REDIRECT_URI = "${local.public_base_url}/auth/callback"
    GUARDRAIL_ID              = var.guardrail_id
    GUARDRAIL_VERSION         = var.guardrail_version
    LANGSMITH_PROJECT         = var.langsmith_project
    LANGSMITH_TRACING         = "true"
    LLM_FALLBACK              = "groq"
    LLM_PROVIDER              = "bedrock"
    LOG_FORMAT                = "json"
    QDRANT_URL                = var.qdrant_url
  }

  # JSON keys of code-review-agent/prod, put in by hand after the first apply.
  app_secret_keys = [
    "GITHUB_WEBHOOK_SECRET",
    "GITHUB_PRIVATE_KEY",
    "GITHUB_OAUTH_CLIENT_SECRET",
    "GROQ_API_KEY",
    "QDRANT_API_KEY",
    "LANGSMITH_API_KEY",
  ]
}

# deploy-app.yml copies the latest revision of this family, swaps the image
# and registers a new revision, so changes made here (env, secrets, sizing)
# reach the service on the next CI deploy.
resource "aws_ecs_task_definition" "app" {
  family                   = "code-review-agent"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  volume {
    name = "data"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.data.id
      root_directory     = "/"
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.data.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name      = local.container_name
    image     = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    cpu       = 0
    essential = true

    portMappings = [{
      name          = "http"
      containerPort = 8000
      hostPort      = 8000
      protocol      = "tcp"
    }]

    environment = [for k, v in local.app_environment : { name = k, value = v }]

    secrets = concat(
      [for k in local.app_secret_keys : {
        name      = k
        valueFrom = "${aws_secretsmanager_secret.app.arn}:${k}::"
      }],
      # A plain-string secret, so the whole value (no JSON key).
      [{ name = "ORIGIN_VERIFY_SECRET", valueFrom = aws_secretsmanager_secret.origin_verify.arn }],
    )

    mountPoints = [{
      sourceVolume  = "data"
      containerPath = "/data"
      readOnly      = false
    }]

    # SIGTERM -> up to 90 s for running reviews (uvicorn), then SIGKILL at 120 s.
    stopTimeout = 120

    healthCheck = {
      command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = local.region
        awslogs-stream-prefix = "app"
        # A CloudWatch Logs hiccup drops log lines instead of blocking the app.
        mode            = "non-blocking"
        max-buffer-size = "25m"
      }
    }

    volumesFrom    = []
    systemControls = []
  }])
}

resource "aws_ecs_service" "app" {
  name             = "cra-service"
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.app.arn
  desired_count    = var.desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  # One task (SQLite + in-process WebSockets): stop the old task, then start
  # the new one. The circuit breaker rolls back a deploy that never gets healthy.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  health_check_grace_period_seconds = 60
  availability_zone_rebalancing     = "DISABLED"

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  network_configuration {
    subnets          = [for s in aws_subnet.public : s.id]
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true # no NAT gateway: the public IP is the way out
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = local.container_name
    container_port   = 8000
  }

  # deploy-app.yml registers a new revision for every image and points the
  # service at it. Without this, the next apply would roll the service back to
  # the Terraform revision (and the bootstrap image).
  lifecycle {
    ignore_changes = [task_definition]
  }

  # The target group must be attached to the ALB before a service can use it.
  depends_on = [aws_lb_listener.http]
}

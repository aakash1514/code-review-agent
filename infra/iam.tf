# ECS task roles. The deploy role GitHub Actions assumes is in github_oidc.tf.

data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ---- execution role: used by the ECS agent to start the task ---------------

resource "aws_iam_role" "execution" {
  name               = "cra-ecs-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

# ECR pull and CloudWatch Logs.
resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-app-secret"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "ReadAppSecret"
      Effect = "Allow"
      Action = "secretsmanager:GetSecretValue"
      Resource = [
        aws_secretsmanager_secret.app.arn,
        aws_secretsmanager_secret.origin_verify.arn,
      ]
    }]
  })
}

# ---- task role: what the app itself can call -------------------------------

resource "aws_iam_role" "task" {
  name               = "cra-ecs-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

# CloudWatch EMF metrics need no statement: they're written to stdout and the
# awslogs driver (execution role) ships them.
resource "aws_iam_role_policy" "task_runtime" {
  name = "app-runtime"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "TitanEmbeddings"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = "arn:aws:bedrock:${local.region}::foundation-model/${var.bedrock_embed_model}"
      },
      {
        Sid      = "MountDataViaAccessPoint"
        Effect   = "Allow"
        Action   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
        Resource = aws_efs_file_system.data.arn
        Condition = {
          StringEquals = { "elasticfilesystem:AccessPointArn" = aws_efs_access_point.data.arn }
        }
      },
      {
        Sid    = "ApplyDiffGuardrail"
        Effect = "Allow"
        Action = "bedrock:ApplyGuardrail"
        # The guardrail uses a cross-region profile, and ApplyGuardrail checks
        # the profile ARN in whichever region it routes to.
        Resource = [
          "arn:aws:bedrock:${local.region}:${local.account_id}:guardrail/${var.guardrail_id}",
          "arn:aws:bedrock:*:*:guardrail-profile/${var.guardrail_profile_id}",
        ]
      },
      {
        Sid      = "GptOssOnBedrock"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = [for m in var.bedrock_llm_models : "arn:aws:bedrock:${local.region}::foundation-model/${m}"]
      },
    ]
  })
}

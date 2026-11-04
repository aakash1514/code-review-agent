# GitHub Actions -> AWS through OIDC. The identity provider and the evals role
# are managed outside Terraform (README.md); only the deploy role is here.

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

# Read only, so `plan` fails early if it's missing and its ARN is an output.
data "aws_iam_role" "gha_evals" {
  name = "cra-gha-evals"
}

locals {
  # The two forms of the same subject for environment `production`:
  #  - ID-based: repo:owner@owner_id/name@repo_id:environment:...
  #  - legacy name-only: repo:owner/name:environment:...
  # The repo has the immutable-subject setting on, but CloudTrail showed GitHub
  # still sending the name-only form, and AssumeRoleWithWebIdentity failed
  # until the trust policy accepted both. Both stay pinned to this repo and
  # this one environment.
  github_owner = split("/", var.github_repository)[0]
  github_repo  = split("/", var.github_repository)[1]
  deploy_subjects = [
    "repo:${local.github_owner}@${var.github_owner_id}/${local.github_repo}@${var.github_repository_id}:environment:production",
    "repo:${var.github_repository}:environment:production",
  ]
}

data "aws_iam_policy_document" "gha_deploy_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.deploy_subjects
    }
  }
}

resource "aws_iam_role" "gha_deploy" {
  name               = "cra-gha-deploy"
  assume_role_policy = data.aws_iam_policy_document.gha_deploy_trust.json
}

# What deploy-app.yml and deploy-web.yml need, and nothing else.
resource "aws_iam_role_policy" "gha_deploy" {
  name = "deploy"
  role = aws_iam_role.gha_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*" # account-level call, no resource ARN exists
      },
      {
        Sid    = "EcrPushPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:DescribeImages",
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Sid      = "TaskDefinitions"
        Effect   = "Allow"
        Action   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
        Resource = "*" # neither action supports resource-level permissions
      },
      {
        Sid      = "DeployService"
        Effect   = "Allow"
        Action   = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = aws_ecs_service.app.arn
      },
      {
        Sid      = "PassTaskRoles"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
      {
        Sid      = "DashboardBucketList"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.dashboard.arn
      },
      {
        Sid      = "DashboardBucketObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.dashboard.arn}/*"
      },
      {
        Sid      = "InvalidateCdn"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
        Resource = aws_cloudfront_distribution.this.arn
      },
    ]
  })
}

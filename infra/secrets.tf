# App secrets: Terraform creates the container only. The values are put in
# with the CLI after the first apply (README.md), so they never appear in this
# code or in the state. The task definition reads them by JSON key.
#
# recovery_window_in_days = 0 deletes immediately on destroy, so the name is
# free again for the next apply instead of locked for 7+ days.
#trivy:ignore:AVD-AWS-0098 aws/secretsmanager key: only this account's execution role reads it; a CMK adds $1/month
resource "aws_secretsmanager_secret" "app" {
  name                    = "code-review-agent/prod"
  description             = "App secrets for ECS (values set with the CLI, not Terraform)"
  recovery_window_in_days = 0
}

# The one secret Terraform generates. CloudFront has to send it as the
# X-Origin-Verify header, so the value is in the distribution config and in
# state either way (the state bucket is private, versioned and encrypted).
# Generating it here keeps CloudFront and the app in sync, and its own secret
# keeps it out of the hand-managed one above.
resource "random_password" "origin_verify" {
  length  = 48
  special = false
}

#trivy:ignore:AVD-AWS-0098 Same as above
resource "aws_secretsmanager_secret" "origin_verify" {
  name                    = "code-review-agent/origin-verify"
  description             = "X-Origin-Verify value shared by CloudFront and the app (Terraform-generated)"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "origin_verify" {
  secret_id     = aws_secretsmanager_secret.origin_verify.id
  secret_string = random_password.origin_verify.result
}

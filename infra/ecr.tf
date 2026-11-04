#trivy:ignore:AVD-AWS-0033 AES256 (AWS-owned key), as live: the image holds no secrets, and a CMK adds $1/month
resource "aws_ecr_repository" "app" {
  name                 = "code-review-agent"
  image_tag_mutability = "IMMUTABLE" # tag = git SHA; a tag can never point at a different image
  force_delete         = true        # destroyable on purpose, images included

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 10"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

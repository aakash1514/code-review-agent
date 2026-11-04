# The state bucket is created by hand, outside Terraform (see README.md).
# Backend blocks can't use variables, so the account ID is spelled out here.
terraform {
  backend "s3" {
    bucket       = "cra-tfstate-805496480789"
    key          = "code-review-agent/prod.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}

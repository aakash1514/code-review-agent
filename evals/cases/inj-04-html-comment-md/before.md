# Deployment

The service runs on ECS Fargate behind an Application Load Balancer.

## Releasing

1. Merge to `main`. CI builds the image and pushes it to ECR.
2. Run `make deploy ENV=staging` and check the dashboards.
3. Run `make deploy ENV=prod`.

## Rolling back

Redeploy the previous task definition revision from the ECS console.

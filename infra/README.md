# infra: the AWS stack as Terraform

One root module that builds the whole production stack in `ap-south-1`. It uses plain `aws_*` resources and no community modules, with one file per concern. It reproduces the stack that was first built by hand in the Console. Names and settings are the same, except for the [intended differences](#differences-from-the-console-stack).

```mermaid
flowchart LR
    viewer["Browser / GitHub webhooks"] --> cf["CloudFront<br/>cloudfront.tf"]
    cf -- "default: OAC" --> s3["S3 dashboard bucket<br/>s3_dashboard.tf"]
    cf -- "/api /auth /ws /webhook /healthz<br/>HTTP + X-Origin-Verify" --> alb["ALB :80<br/>alb.tf"]
    alb -- ":8000" --> task["ECS Fargate task<br/>ecs.tf"]
    task -- "NFS 2049, TLS + IAM" --> efs["EFS /data<br/>efs.tf"]
    task -. "secrets at start" .- sm["Secrets Manager<br/>secrets.tf"]
    task --> cw["CloudWatch logs, alarms,<br/>dashboard, queries<br/>observability.tf"]
    cw --> sns["SNS email"]
    gha["GitHub Actions<br/>OIDC"] -- "cra-gha-deploy<br/>github_oidc.tf" --> ecr["ECR<br/>ecr.tf"]
```

| File | What it builds |
|---|---|
| `network.tf` | VPC `cra-vpc` 10.0.0.0/16, two public subnets (1a, 1b), IGW, public route table. No NAT, no endpoints |
| `security_groups.tf` | `cra-alb-sg` (80 from CloudFront's origin-facing prefix list; egress only 8000 to the task), `cra-task-sg` (8000 from the ALB; egress open), `cra-efs-sg` (2049 from the task; no egress) |
| `efs.tf` | `cra-efs`: encrypted, elastic throughput, no backups, a policy that admits only the task role through the access point and only over TLS, a mount target per subnet, access point `cra-data-ap` |
| `ecr.tf` | `code-review-agent`: immutable tags, scan on push, keeps the last 10 images |
| `secrets.tf` | `code-review-agent/prod` (container only) and `code-review-agent/origin-verify` (generated) |
| `iam.tf` | `cra-ecs-execution-role` and `cra-ecs-task-role` (least privilege) |
| `alb.tf` | `cra-alb` (idle timeout 300, drops invalid headers), `cra-tg`, the HTTP:80 listener |
| `ecs.tf` | `cra-cluster`, task definition `code-review-agent`, service `cra-service` |
| `s3_dashboard.tf` | `cra-dashboard-<account>`: private, SSE-S3, readable only by the distribution |
| `cloudfront.tf` | The distribution, its OAC, and the managed cache and origin-request policies |
| `observability.tf` | Log group (14 days), SNS topic and email, 7 alarms, the dashboard, 5 saved Logs Insights queries |
| `github_oidc.tf` | `cra-gha-deploy` (the deploy role); reads the OIDC provider and `cra-gha-evals` |

Cost: about **$1.5/day** while it runs. Most of it is the ALB (about $0.55), the Fargate task at 0.5 vCPU and 1 GB (about $0.60), and three public IPv4 addresses (about $0.36). EFS, CloudWatch, Secrets Manager, S3 and CloudFront add cents. Bedrock is billed per review, at about $0.0017 per file.

## Prerequisites

- Terraform ≥ 1.10. The providers are pinned in `versions.tf`, and `.terraform.lock.hcl` is committed.
- AWS CLI v2 with an admin profile for this account (here `cra-admin`), plus Docker for the first image push.
- `cp terraform.tfvars.example terraform.tfvars` and fill it in. `*.tfvars` is git-ignored.

### The state bucket

State lives in `s3://cra-tfstate-805496480789/code-review-agent/prod.tfstate`, with native S3 locking (`use_lockfile`, no DynamoDB). The bucket was created by hand once, because Terraform can't store its state in a bucket it hasn't created yet:

```bash
aws s3api create-bucket --bucket cra-tfstate-805496480789 --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1
aws s3api put-bucket-versioning --bucket cra-tfstate-805496480789 \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket cra-tfstate-805496480789 \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
aws s3api put-public-access-block --bucket cra-tfstate-805496480789 \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

The state holds one secret: the generated `X-Origin-Verify` value (see [secrets.tf](secrets.tf)). It's there because CloudFront's config contains it either way. Keep the bucket private.

## Managed outside Terraform

These four exist before the first apply, and a teardown of the stack keeps them. Terraform only reads or references them:

| What | How it's used here | Why it's outside |
|---|---|---|
| GitHub OIDC provider `token.actions.githubusercontent.com` | `data "aws_iam_openid_connect_provider"`, the federated principal of `cra-gha-deploy` | Account-wide (one per account), and the evals role depends on it too |
| IAM role `cra-gha-evals` and the GitHub environment `evals` | `data "aws_iam_role"`, output `evals_role_arn` | The CI Eval gate runs on every PR. It must keep working while the stack is down or being rebuilt |
| Bedrock guardrail `cra-diff-guard` | `var.guardrail_id` (default `odz2vmufjpud`) in the task env and the task role policy | Its prompt-attack settings were tuned and measured in the evals. Rebuilding it gives a new ID and an unmeasured config |
| State bucket `cra-tfstate-805496480789` | `backend.tf` | It holds this module's state, so it can't be managed by this module |

## First apply (runbook)

Run every command from `infra/`, with `AWS_PROFILE` set to the admin profile.

**0. Before tearing down the Console stack**, save the current secret values. Terraform never has them. The file holds live secrets, so it goes in a temp folder outside the repo, never inside the working tree:

```bash
SECRETS_DIR="${TEMP:-${TMPDIR:-/tmp}}/cra-secrets"
mkdir -p "$SECRETS_DIR"
aws secretsmanager get-secret-value --secret-id code-review-agent/prod \
  --query SecretString --output text > "$SECRETS_DIR/prod-secret.json"   # deleted at the end of step 3
```

When you delete the Console's secret, use `--force-delete-without-recovery`. Otherwise the name `code-review-agent/prod` stays locked for 7–30 days, and the apply fails.

**1. Apply with no running task.** The secret values and the image don't exist yet:

```bash
terraform init
terraform apply -var-file=terraform.tfvars -var desired_count=0
```

**2. Confirm the SNS subscription.** AWS emails `alert_email` a confirmation link. The alarms don't email anyone until you click it.

**3. Put the secret values.** `$SECRETS_DIR/prod-secret.json` is one JSON object with these six keys. `ORIGIN_VERIFY_SECRET` isn't one of them: it now lives in `code-review-agent/origin-verify`, and Terraform sets it. If the saved file still has that key, leaving it in does no harm.

```json
{
  "GITHUB_WEBHOOK_SECRET": "<webhook secret>",
  "GITHUB_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\\n<key, newlines as \\n>\\n-----END RSA PRIVATE KEY-----\\n",
  "GITHUB_OAUTH_CLIENT_SECRET": "<oauth client secret>",
  "GROQ_API_KEY": "<groq key>",
  "QDRANT_API_KEY": "<qdrant key>",
  "LANGSMITH_API_KEY": "<langsmith key>"
}
```

```bash
aws secretsmanager put-secret-value --secret-id code-review-agent/prod \
  --secret-string "file://$SECRETS_DIR/prod-secret.json"
rm -f "$SECRETS_DIR/prod-secret.json"
rmdir "$SECRETS_DIR"
```

A missing key stops the task from starting (`ResourceInitializationError`), so all six must be present.

**4. Push the first image.** The task definition Terraform registers points at `:bootstrap` (`var.image_tag`):

```bash
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --provenance=false --sbom=false -t "$REPO:bootstrap" ..
docker push "$REPO:bootstrap"
```

The tags are immutable, so `bootstrap` can be pushed only once per repository. There's an alternative to the manual push: run `deploy-app` by hand at this point. It pushes a SHA tag and points the service at it. Its `/healthz` smoke test then fails, which is expected because no task runs yet.

**5. Start the task:**

```bash
terraform apply -var-file=terraform.tfvars    # desired_count defaults to 1
```

**6. Point GitHub at the new stack.** Update the variables in [the table below](#github-variables-to-update), then the GitHub App (Settings → Developer settings → GitHub Apps → the App):

- **Webhook URL:** `terraform output -raw github_webhook_url` (`https://<new>.cloudfront.net/webhook`). The webhook secret is the same as before.
- **Callback URL:** `terraform output -raw github_oauth_callback_url` (`https://<new>.cloudfront.net/auth/callback`).

**7. Deploy the code.** Run `deploy-web` (the bucket starts empty) and `deploy-app` from the Actions tab. From then on, CI owns the image (see below). The knowledge graph and the SQLite history start empty, because EFS is new. The next push to the default branch rebuilds the knowledge graph. The Qdrant vectors live in Qdrant Cloud, so they survive.

### GitHub variables to update

The workflows read everything from `vars.*`, and no workflow needs an edit. There are no GitHub secrets. After step 5:

| Variable | Environment | Changes? | Source |
|---|---|---|---|
| `ECS_SERVICE` | production | **yes**: `code-review-agent-service-12wyej3o` → `cra-service` | `terraform output -raw ecs_service_name` |
| `CF_DISTRIBUTION_ID` | production | **yes**: new distribution | `terraform output -raw cloudfront_distribution_id` |
| `PUBLIC_BASE_URL` | production | **yes**: new `*.cloudfront.net` domain | `terraform output -raw public_base_url` |
| `AWS_ROLE_ARN` | production | no (same name, same ARN) | `deploy_role_arn` |
| `S3_BUCKET` | production | no | `dashboard_bucket` |
| `ECR_REPOSITORY` | production | no | `ecr_repository_name` |
| `ECS_CLUSTER` | production | no | `ecs_cluster_name` |
| `ECS_TASK_FAMILY` | production | no | `ecs_task_family` |
| `CONTAINER_NAME` | production | no | `container_name` |
| `AWS_REGION` | production, evals | no | `ap-south-1` |
| `AWS_EVALS_ROLE_ARN` | evals | no (not managed here) | `evals_role_arn` |

```bash
for pair in ECS_SERVICE=ecs_service_name CF_DISTRIBUTION_ID=cloudfront_distribution_id PUBLIC_BASE_URL=public_base_url; do
  gh variable set "${pair%%=*}" --env production --body "$(terraform output -raw "${pair#*=}")"
done
```

## Terraform and CI deploys

- `deploy-app.yml` copies the **latest revision** of the `code-review-agent` family, swaps in the new image (git SHA tag), registers it as a new revision and points the service at it.
- So the service ignores `task_definition` changes (`lifecycle.ignore_changes`). Otherwise every apply would roll it back to the Terraform revision and the `bootstrap` image.
- A Terraform change to the task definition, such as an env var, a secret or sizing, registers a new revision but doesn't restart anything. It reaches the service on the next `deploy-app` run, which copies that revision. Run `deploy-app` by hand to apply it right away.
- `desired_count` is not ignored, because nothing else changes it: CI keeps the service's count. That lets the runbook's second apply start the task.

## Destroy

```bash
terraform destroy -var-file=terraform.tfvars
```

The stack is destroyable on purpose, and destroying it deletes data:

- **EFS:** the SQLite review history and the knowledge-graph JSON are gone, since there are no backups. Accepted for a portfolio stack. The knowledge graph rebuilds on the next push webhook.
- **ECR** (`force_delete`) and the **S3 dashboard bucket** (`force_destroy`) are emptied and deleted.
- **Secrets** are deleted immediately (`recovery_window_in_days = 0`), so the names are free for the next apply. Save the values first (runbook step 0) if you'll rebuild.
- **The log group** and its history go too.
- **Left behind:** the four things [managed outside Terraform](#managed-outside-terraform), and the task-definition revisions CI registered. Those cost nothing and can be deregistered with `aws ecs deregister-task-definition`.

The GitHub variables then point at nothing until the next apply.

## Rebuild log

**4 Nov 2026: the Console stack was replaced by this module.**

- The Console-built stack was torn down and rebuilt from `infra/`. The first apply created 63 resources with no retries. The rollout followed the runbook: `desired_count=0`, put the secret values, apply with `desired_count=1`, then `deploy-app` and `deploy-web`.
- After CI's deploy, `terraform plan` reported **"No changes"**, so Terraform and CI coexist without drift (the service ignores `task_definition`).
- New live URL `https://d1ngkk9hkqym35.cloudfront.net` (distribution `E1ZV2OTPU3FYCT`), and the service is now `cra-service`.
- The GitHub OIDC provider, `cra-gha-evals`, the guardrail `odz2vmufjpud` and the state bucket stayed outside Terraform and survived the teardown.
- Hardened compared with the Console stack: the EFS policy admits only the task role through the access point, ALB egress is TCP 8000 to the task SG only, and the EFS SG has no egress ([differences](#differences-from-the-console-stack)).
- The new EFS started empty, so the review history was reset ([KNOWN_ISSUES #21](../docs/KNOWN_ISSUES.md)). The knowledge graph rebuilds on the next push.

## Differences from the Console stack

| Setting | Console | Terraform | Why |
|---|---|---|---|
| Service name | `code-review-agent-service-12wyej3o` | `cra-service` | The Console's generated name was a mistake |
| `ORIGIN_VERIFY_SECRET` | A key in `code-review-agent/prod` | Its own secret, `code-review-agent/origin-verify`, generated | CloudFront needs the value in its config, so Terraform has it anyway |
| Execution role secret access | `code-review-agent/prod` | Both secrets | Follows from the row above |
| Image | `:<git sha>` | `:bootstrap` in the Terraform revision | CI takes over after the first deploy |
| ECR repository scan-on-push | Off (the registry rule scans every repo on push) | On | Same effect; set explicitly |
| ALB origin SSL protocols | SSLv3, TLSv1, TLSv1.1, TLSv1.2 | TLSv1.2 | Required field, unused with `http-only` |
| CloudFront origin IDs, OAC name | Console-generated | `alb`, `s3-dashboard`, `cra-dashboard-oac` | Labels only |
| Tags | `Project` | `Project`, `ManagedBy = terraform` | `default_tags` |
| EFS file-system policy | Wizard default: any principal may `ClientRootAccess`, `ClientWrite` and `ClientMount` through a mount target; Deny without TLS | Only `cra-ecs-task-role` may `ClientMount` and `ClientWrite`, only through `cra-data-ap`; same Deny without TLS | Least privilege. No root access, because the access point maps everything to uid 1000 |
| `cra-alb-sg` egress | All traffic to `0.0.0.0/0` | TCP 8000 to `cra-task-sg` only | The ALB only forwards to the task and health-checks it |
| `cra-efs-sg` egress | All traffic to `0.0.0.0/0` | None | Mount targets never open connections; replies to the task's NFS traffic are allowed statefully |
| `cra-task-sg` egress | All traffic | Unchanged: all traffic | No NAT or endpoints. It reaches AWS APIs, GitHub, Groq, LangSmith and Qdrant Cloud, which uses a non-443 port |

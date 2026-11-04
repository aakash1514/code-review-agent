variable "region" {
  description = "AWS region for the whole stack."
  type        = string
  default     = "ap-south-1"
}

variable "alert_email" {
  description = "Email address subscribed to the alarm SNS topic. Confirm the subscription email after apply."
  type        = string
}

# ---- ECS service ----------------------------------------------------------

variable "image_tag" {
  description = "Image tag for the Terraform-registered task definition. Only the first deploy uses it; after that deploy-app.yml registers revisions with the git SHA tag."
  type        = string
  default     = "bootstrap"
}

variable "desired_count" {
  description = "Running tasks. Use 0 for the first apply (no secret values or image yet), then 1."
  type        = number
  default     = 1

  validation {
    condition     = contains([0, 1], var.desired_count)
    error_message = "0 or 1: SQLite and the WebSocket clients live in one process."
  }
}

# ---- GitHub ---------------------------------------------------------------

variable "github_repository" {
  description = "owner/name of the repository whose Actions deploy this stack."
  type        = string
  default     = "aakash1514/code-review-agent"
}

# Public: gh api repos/aakash1514/code-review-agent --jq '.owner.id, .id'
variable "github_owner_id" {
  description = "Numeric GitHub owner ID, for the ID-based OIDC sub."
  type        = string
  default     = "122259569"
}

variable "github_repository_id" {
  description = "Numeric GitHub repository ID, for the ID-based OIDC sub."
  type        = string
  default     = "1301667387"
}

variable "github_app_id" {
  description = "GitHub App ID (GITHUB_APP_ID)."
  type        = string
}

variable "github_installation_id" {
  description = "GitHub App installation ID (GITHUB_INSTALLATION_ID)."
  type        = string
}

variable "github_oauth_client_id" {
  description = "The GitHub App's OAuth client ID (GITHUB_OAUTH_CLIENT_ID). Not a secret; the client secret is in Secrets Manager."
  type        = string
}

# ---- External services ----------------------------------------------------

variable "qdrant_url" {
  description = "Qdrant Cloud cluster URL (QDRANT_URL). The API key is in Secrets Manager."
  type        = string
}

variable "langsmith_project" {
  description = "LangSmith project for traces (LANGSMITH_PROJECT)."
  type        = string
  default     = "code-review-agent"
}

# ---- Bedrock --------------------------------------------------------------

variable "guardrail_id" {
  description = "Bedrock guardrail ID (created outside Terraform)."
  type        = string
  default     = "odz2vmufjpud"
}

variable "guardrail_version" {
  description = "Bedrock guardrail version."
  type        = string
  default     = "1"
}

variable "guardrail_profile_id" {
  description = "Cross-region guardrail profile the guardrail uses; ApplyGuardrail needs permission on it too."
  type        = string
  default     = "apac.guardrail.v1:0"
}

variable "bedrock_llm_models" {
  description = "Bedrock foundation models the agents call (reasoning and light tier)."
  type        = list(string)
  default     = ["openai.gpt-oss-120b-1:0", "openai.gpt-oss-20b-1:0"]
}

variable "bedrock_embed_model" {
  description = "Bedrock embedding model."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

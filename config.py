# config.py
#
# All settings come from environment variables. Locally, python-dotenv loads
# them from .env (git-ignored; see .env.example for every key). In the
# container, ECS injects them, with secrets from AWS Secrets Manager.
#
# This file is tracked in git and must never contain a secret value.

import os
from pathlib import Path

from dotenv import load_dotenv

# Does not override variables that are already set in the environment.
load_dotenv()


def _env(name: str, default: str = "") -> str:
    """The variable's value, or default when it is unset or empty."""
    value = os.getenv(name)
    return default if value is None or value.strip() == "" else value.strip()


class Settings:
    # Data directory: the SQLite DB and knowledge_graph_*.json files.
    # ./data locally, /data in the container (an EFS mount on AWS).
    DATA_DIR: str = _env("DATA_DIR", "./data")

    # GitHub App
    # Numeric App ID from the GitHub App's General settings page.
    GITHUB_APP_ID: str = _env("GITHUB_APP_ID")
    # The App's private key (PEM). GITHUB_PRIVATE_KEY holds the key itself and
    # wins when set (the container uses this); GITHUB_PRIVATE_KEY_PATH points
    # to a .pem file (local dev).
    GITHUB_PRIVATE_KEY: str = _env("GITHUB_PRIVATE_KEY")
    GITHUB_PRIVATE_KEY_PATH: str = _env("GITHUB_PRIVATE_KEY_PATH", "./github-app-private-key.pem")
    # Installation ID of the App on the target account/org.
    GITHUB_INSTALLATION_ID: str = _env("GITHUB_INSTALLATION_ID")
    # Secret used to verify X-Hub-Signature-256 on incoming webhooks.
    GITHUB_WEBHOOK_SECRET: str = _env("GITHUB_WEBHOOK_SECRET")

    # GitHub OAuth (dashboard login), using the GitHub App's own OAuth
    # credentials (or a standalone OAuth App with the same callback URL).
    GITHUB_OAUTH_CLIENT_ID: str = _env("GITHUB_OAUTH_CLIENT_ID")
    GITHUB_OAUTH_CLIENT_SECRET: str = _env("GITHUB_OAUTH_CLIENT_SECRET")
    GITHUB_OAUTH_REDIRECT_URI: str = _env("GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")

    # Groq (LLM reasoning, including synthesis)
    GROQ_API_KEY: str = _env("GROQ_API_KEY")
    # Heavy reasoning / synthesis calls (security, architecture).
    GROQ_MODEL_REASONING: str = _env("GROQ_MODEL_REASONING", "openai/gpt-oss-120b")
    # Cheap / fast calls (test coverage, documentation).
    GROQ_MODEL_LIGHT: str = _env("GROQ_MODEL_LIGHT", "openai/gpt-oss-20b")

    # Review scope (pipeline/file_filter.py). Skipped files are listed, with
    # the reason, in the PR comment and on the dashboard.
    # Glob patterns never reviewed: "dir/" matches that directory anywhere in
    # the path, anything else matches the file name or the full path.
    REVIEW_SKIP_PATTERNS: list[str] = [
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "*.lock",  # lockfiles
        "dist/", "build/", "node_modules/", "*.min.js", "*.map",                     # generated / vendored
        "LICENSE", "LICENSE.*",
    ]
    # Patches longer than this are skipped (also keeps prompts under Groq's size limit).
    MAX_PATCH_CHARS: int = int(_env("MAX_PATCH_CHARS", "20000"))
    # At most this many files are reviewed per PR: Python first, then other
    # code, then config, then docs. The rest are listed as "not reviewed (limit)".
    MAX_FILES_PER_REVIEW: int = int(_env("MAX_FILES_PER_REVIEW", "25"))

    # Embeddings: "local" (sentence-transformers on CPU, needs
    # requirements-local-embed.txt), "hf_api" (Hugging Face Inference API,
    # needs HF_TOKEN) or "bedrock" (Titan Text Embeddings V2).
    EMBEDDING_PROVIDER: str = _env("EMBEDDING_PROVIDER", "local")
    HF_TOKEN: str = _env("HF_TOKEN")

    # AWS (Bedrock). Credentials come from the normal AWS chain: AWS_PROFILE
    # locally, the ECS task role in the cloud. Never put keys here.
    AWS_REGION: str = _env("AWS_REGION", "ap-south-1")
    # Region for Titan embeddings; defaults to AWS_REGION.
    BEDROCK_EMBED_REGION: str = _env("BEDROCK_EMBED_REGION")
    # Titan V2 vector size: 256, 512 or 1024. Changing it means a new
    # Qdrant collection (codebase_titan<dim>) and a reindex.
    BEDROCK_EMBED_DIM: str = _env("BEDROCK_EMBED_DIM", "512")

    # Qdrant
    QDRANT_URL: str = _env("QDRANT_URL")
    QDRANT_API_KEY: str = _env("QDRANT_API_KEY")

    # Dashboard
    # Public base URL of the React dashboard (used in links / OAuth redirects).
    DASHBOARD_BASE_URL: str = _env("DASHBOARD_BASE_URL", "http://localhost:5173")

    # Database: SQLite URL ("sqlite:///" prefix is stripped). Defaults to
    # review_agent.db inside DATA_DIR.
    DATABASE_URL: str = _env("DATABASE_URL", "sqlite:///" + (Path(DATA_DIR) / "review_agent.db").as_posix())

    # Behind CloudFront: CloudFront adds an X-Origin-Verify header with this
    # value, and the app rejects requests without it. Empty = check disabled
    # (local dev).
    ORIGIN_VERIFY_SECRET: str = _env("ORIGIN_VERIFY_SECRET")


settings = Settings()

# SQLite can't create its database file in a directory that doesn't exist.
Path(settings.DATA_DIR).mkdir(parents=True, exist_ok=True)
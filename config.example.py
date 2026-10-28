# config.example.py
#
# Copy to config.py (git-ignored) and set real values in .env or the
# environment. Every value below is a placeholder.

import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # GitHub App
    # Numeric App ID from the GitHub App's General settings page.
    GITHUB_APP_ID: str = os.getenv("GITHUB_APP_ID", "123456")
    # Path to the App's private key (.pem) used to sign RS256 JWTs.
    GITHUB_PRIVATE_KEY_PATH: str = os.getenv("GITHUB_PRIVATE_KEY_PATH", "./github-app-private-key.pem")
    # Installation ID of the App on the target account/org.
    GITHUB_INSTALLATION_ID: str = os.getenv("GITHUB_INSTALLATION_ID", "12345678")
    # Secret used to verify X-Hub-Signature-256 on incoming webhooks.
    GITHUB_WEBHOOK_SECRET: str = os.getenv("GITHUB_WEBHOOK_SECRET", "change-me-webhook-secret")

    # GitHub OAuth (dashboard user login, separate from App auth above)
    # Uses the GitHub App's own OAuth credentials: set the App's callback URL to
    # http://localhost:8000/auth/callback and generate a client secret there.
    # A standalone OAuth App with the same callback URL also works.
    # OAuth client ID from the same GitHub App's General settings page.
    GITHUB_OAUTH_CLIENT_ID: str = os.getenv("GITHUB_OAUTH_CLIENT_ID", "Iv1.xxxxxxxxxxxxxxxx")
    # Client secret generated in the same GitHub App (or OAuth App).
    GITHUB_OAUTH_CLIENT_SECRET: str = os.getenv("GITHUB_OAUTH_CLIENT_SECRET", "change-me-oauth-client-secret")
    # Callback URL registered with GitHub for the OAuth flow.
    GITHUB_OAUTH_REDIRECT_URI: str = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")

    # Groq (all LLM reasoning, including synthesis)
    # Groq API key.
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "gsk_your_groq_api_key")
    # Groq model ID for heavy reasoning / synthesis calls (security, architecture).
    GROQ_MODEL_REASONING: str = os.getenv("GROQ_MODEL_REASONING", "openai/gpt-oss-120b")
    # Groq model ID for cheap / fast calls (test coverage, documentation).
    GROQ_MODEL_LIGHT: str = os.getenv("GROQ_MODEL_LIGHT", "openai/gpt-oss-20b")

    # Embeddings
    # "local" (default): run the embedding model in-process with
    # sentence-transformers on CPU; no API credits needed.
    # "hf_api": call the Hugging Face Inference API with HF_TOKEN below.
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "local")

    # Hugging Face
    # HF access token, only used when EMBEDDING_PROVIDER is "hf_api".
    HF_TOKEN: str = os.getenv("HF_TOKEN", "hf_your_hugging_face_token")

    # Qdrant
    # Qdrant cluster URL.
    QDRANT_URL: str = os.getenv("QDRANT_URL", "https://your-cluster.qdrant.io:6333")
    # Qdrant API key.
    QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "your-qdrant-api-key")

    # Dashboard
    # Public base URL of the React dashboard (used in links / OAuth redirects).
    DASHBOARD_BASE_URL: str = os.getenv("DASHBOARD_BASE_URL", "http://localhost:5173")

    # Database
    # SQLite URL for durable job/session state ("sqlite:///" prefix is stripped).
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./review_agent.db")


settings = Settings()

# Backend image: FastAPI + the agent pipeline. The dashboard is not in here;
# it's built separately and served from S3 behind CloudFront.
#
#   docker build -t code-review-agent:local .
#
# Runs as a non-root user (uid 1000, matching the EFS access point in 8.3).
# Configuration comes only from environment variables (see .env.example).

ARG PYTHON_VERSION=3.13

# ---- build stage: install dependencies into a virtualenv -------------------
FROM python:${PYTHON_VERSION}-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Only the requirements first, so code changes don't reinstall dependencies.
COPY requirements.txt .
RUN pip install -r requirements.txt \
    # pip isn't needed at run time, and it vendors its own copies of urllib3,
    # msgpack and setuptools that image scanners flag. Remove it from the venv.
    && pip uninstall -y pip

# ---- runtime stage: the venv plus the application code ---------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# Container defaults; any of them can be overridden at run time.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOG_FORMAT=json \
    DATA_DIR=/data \
    EMBEDDING_PROVIDER=bedrock \
    AWS_REGION=ap-south-1

# The base image's own pip goes too (same vendored-library findings); nothing
# installs packages at run time.
RUN python -m pip uninstall -y pip \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
# Explicit list: nothing else from the repo (tests, dashboard, .env, keys)
# can end up in the image, even if .dockerignore misses something.
COPY --chown=app:app config.py ./
COPY --chown=app:app agents ./agents
COPY --chown=app:app api ./api
COPY --chown=app:app models ./models
COPY --chown=app:app pipeline ./pipeline
COPY --chown=app:app rag ./rag
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app tools ./tools

USER app

EXPOSE 8000

# For docker / docker compose. ECS ignores this and uses the task
# definition's health check (and the ALB's) instead. slim has no curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

# One worker: WebSocket clients and in-flight reviews live in this process,
# and SQLite expects a single writer.
#   --ws-ping-interval 20           keeps idle dashboard sockets alive through the ALB/CloudFront
#   --timeout-graceful-shutdown 90  on SIGTERM, let running reviews finish for up to 90 s
#                                   (ECS stopTimeout is 120 s), then mark the rest interrupted
#   --proxy-headers                 trust X-Forwarded-* from the ALB for client IP and scheme
CMD ["uvicorn", "api.webhook:app", \
     "--host", "0.0.0.0", "--port", "8001", "--workers", "1", \
     "--ws-ping-interval", "20", \
     "--timeout-graceful-shutdown", "90", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
"""Turns the failures a tool can run into into one clear line for the model,
never a traceback (MCP clients show the tool's error text as is)."""
import sys
import traceback
from contextlib import contextmanager

from mcp.server.mcpserver.exceptions import ToolError

from rag.retriever import short_error

AWS_LOGIN_HINT = "Run `aws login --profile cra-admin` and set AWS_PROFILE=cra-admin for the server."

# botocore's credential errors, matched by class name so botocore is only
# imported when AWS is actually used.
_AWS_CREDENTIAL_ERRORS = {
    "NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError", "TokenRetrievalError",
    "UnauthorizedSSOTokenError", "SSOTokenLoadError", "ProfileNotFound", "NoRegionError",
}
_AWS_AUTH_CODES = {"ExpiredToken", "ExpiredTokenException", "UnrecognizedClientException",
                   "InvalidSignatureException", "AccessDeniedException"}


class ToolInputError(Exception):
    """A problem the caller can fix (unknown repo, function not found, ...).
    Its message is shown unchanged."""


def _class_names(e: Exception) -> set[str]:
    return {cls.__name__ for cls in type(e).__mro__}


def _github_error(e) -> str:
    resp = e.response
    path = resp.url.split("api.github.com", 1)[-1].split("?", 1)[0] if resp is not None and resp.url else "?"
    status = resp.status_code if resp is not None else "?"
    if status == 404:
        return (f"GitHub returned 404 Not Found for {path}: the repo, PR or commit doesn't exist, "
                "or the GitHub App isn't installed on that repo.")
    if status == 401:
        return ("GitHub rejected the App's credentials (401): check GITHUB_APP_ID, GITHUB_INSTALLATION_ID "
                "and the App private key.")
    try:
        message = (resp.json() or {}).get("message", "")
    except ValueError:
        message = ""
    return f"GitHub API error {status} for {path}" + (f": {message}" if message else ".")


def describe_error(e: Exception) -> str:
    """One line saying what failed and, where there is one, how to fix it."""
    names = _class_names(e)
    if isinstance(e, ToolInputError):
        return str(e)
    if names & _AWS_CREDENTIAL_ERRORS:
        return f"AWS credentials missing or expired ({short_error(e)}). {AWS_LOGIN_HINT}"
    if "ClientError" in names:
        error = (getattr(e, "response", None) or {}).get("Error") or {}
        code = error.get("Code", "")
        text = f"AWS {code}: {error.get('Message', '')}".strip().rstrip(":")
        return f"{text}. {AWS_LOGIN_HINT}" if code in _AWS_AUTH_CODES else text
    if "HTTPError" in names and getattr(e, "response", None) is not None:
        return _github_error(e)
    if names & {"ConnectionError", "Timeout"} and "requests" in type(e).__module__:
        return f"Could not reach GitHub ({short_error(e)})."
    if "UnexpectedResponse" in names:  # qdrant_client
        status = getattr(e, "status_code", "?")
        if status == 404:
            return f"Qdrant: collection not found ({short_error(e)}). Check QDRANT_URL and EMBEDDING_PROVIDER."
        return f"Qdrant error {status}: {short_error(e)}"
    if isinstance(e, FileNotFoundError) and str(getattr(e, "filename", "")).endswith(".pem"):
        return (f"GitHub App private key not found at {e.filename}. Set GITHUB_PRIVATE_KEY_PATH "
                "(absolute, or relative to the repo root) or GITHUB_PRIVATE_KEY.")
    # Unexpected: the client gets one line, the server log (stderr) the traceback.
    traceback.print_exc(file=sys.stderr)
    return short_error(e)


@contextmanager
def tool_errors():
    """Every exception raised inside becomes a ToolError with describe_error's
    message, which the SDK returns as the tool's error result."""
    try:
        yield
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(describe_error(e)) from None


def require_aws_credentials() -> None:
    """Fails fast, with the fix, when boto3 can't find usable AWS credentials;
    otherwise the agents would each fail and the review would look clean."""
    try:
        import boto3
        credentials = boto3.Session().get_credentials()
        frozen = credentials.get_frozen_credentials() if credentials else None
    except Exception as e:
        raise ToolInputError(f"AWS credentials could not be loaded ({short_error(e)}). {AWS_LOGIN_HINT}") from None
    if frozen is None or not frozen.access_key:
        raise ToolInputError(f"No AWS credentials found. {AWS_LOGIN_HINT}")

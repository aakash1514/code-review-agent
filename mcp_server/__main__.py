"""`python -m mcp_server`: the MCP server over stdio.

stdout is the protocol channel, and this codebase print()s progress
everywhere (model_client, runner, the agents, the knowledge graph), so no
application output may reach it:

1. Imports run with sys.stdout pointed at stderr.
2. The SDK's stdio_server() then claims fd 1 for the transport (it keeps a
   private duplicate for its messages and points fd 1 itself at stderr while
   serving), so even writes that bypass sys.stdout miss the wire.
3. While the session runs, sys.stdout is stderr as well (the lifespan below).
   It's restored before the SDK releases fd 1, so nothing buffered in it can
   land on the wire on exit.

Defaults for the MCP server (an explicitly set environment variable wins;
.env does not override them, as these are set before config.py loads it):
  EMBEDDING_PROVIDER=bedrock  search_code queries codebase_titan512
  LLM_PROVIDER=bedrock, LLM_FALLBACK=none  review_patch on Bedrock gpt-oss
LOG_FORMAT is always "text" here: the JSON mode writes to the real stdout.
"""
import os
import sys
from contextlib import asynccontextmanager, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MCP_ENV_DEFAULTS = {
    "EMBEDDING_PROVIDER": "bedrock",
    "LLM_PROVIDER": "bedrock",
    "LLM_FALLBACK": "none",
}


@asynccontextmanager
async def _stdout_to_stderr(_server):
    with redirect_stdout(sys.stderr):
        yield {}


def main() -> None:
    for name, value in MCP_ENV_DEFAULTS.items():
        if not os.environ.get(name, "").strip():
            os.environ[name] = value
    os.environ["LOG_FORMAT"] = "text"
    # Relative settings (GITHUB_PRIVATE_KEY_PATH, DATA_DIR) resolve against
    # the repo root, wherever the client launched the server from.
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    with redirect_stdout(sys.stderr):
        from mcp_server.server import build_server
        server = build_server(lifespan=_stdout_to_stderr)
    print(f"[mcp] {server.name} ready on stdio (LLM_PROVIDER={os.environ['LLM_PROVIDER']}, "
          f"EMBEDDING_PROVIDER={os.environ['EMBEDDING_PROVIDER']})", file=sys.stderr)
    server.run("stdio")


if __name__ == "__main__":
    main()

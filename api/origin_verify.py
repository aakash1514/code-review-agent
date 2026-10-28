"""Only accept traffic that came through our CloudFront distribution.

On AWS the ALB's security group already admits only CloudFront's IP ranges,
but every CloudFront distribution in the world shares those ranges. So
CloudFront also adds a secret header (X-Origin-Verify) to every request it
forwards to the ALB, and this middleware rejects requests without it.

Pure ASGI (not @app.middleware("http")) so it also covers WebSocket
handshakes. Disabled when ORIGIN_VERIFY_SECRET is empty (local dev).
/healthz is always open: the ALB health check calls the task directly,
without going through CloudFront.
"""
import hmac
import json

from config import settings

HEADER = b"x-origin-verify"
EXEMPT_PATHS = {"/healthz"}


def _secret() -> str:
    # Read per request, so tests (and a settings reload) take effect.
    return getattr(settings, "ORIGIN_VERIFY_SECRET", "") or ""


class OriginVerifyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        secret = _secret()
        if (
            not secret
            or scope["type"] not in ("http", "websocket")
            or scope.get("path") in EXEMPT_PATHS
        ):
            await self.app(scope, receive, send)
            return

        sent = dict(scope.get("headers") or []).get(HEADER, b"")
        if hmac.compare_digest(sent, secret.encode()):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Closing before accept makes the handshake fail with HTTP 403.
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": "forbidden"}).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
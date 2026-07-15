import asyncio
import json
from fastapi import WebSocket


class ConnectionManager:
    """
    In-memory pub/sub for live pipeline progress. Keyed by repo, matching how
    the REST list endpoint is scoped — a single connection receives every
    event for that repo, and the frontend routes each event to the right
    list row / detail panel by commit_sha.

    Single-process only, deliberately, matching the rest of the zero-cost
    stack (SQLite job store, in-process token cache). If this app ever runs
    with >1 worker process, this needs a real pub/sub backend (e.g. Redis)
    instead — noting that now so it isn't a silent gap later.
    """
    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, repo: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(repo, set()).add(websocket)

    async def disconnect(self, repo: str, websocket: WebSocket) -> None:
        async with self._lock:
            conns = self._connections.get(repo)
            if conns and websocket in conns:
                conns.discard(websocket)
                if not conns:
                    self._connections.pop(repo, None)

    async def broadcast(self, repo: str, event: dict) -> None:
        async with self._lock:
            conns = list(self._connections.get(repo, ()))
        if not conns:
            return
        payload = json.dumps(event)
        dead = []
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.get(repo, set()).discard(ws)


manager = ConnectionManager()
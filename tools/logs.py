"""Structured logs for CloudWatch, without rewriting every print().

LOG_FORMAT=text (default, local dev): output is unchanged.
LOG_FORMAT=json (the container): every line written to stdout/stderr --
our print()s, tracebacks and uvicorn's own logs -- becomes one JSON object:

  {"ts": "2026-10-29T10:15:02.123Z", "level": "INFO", "msg": "[webhook] ...",
   "repo": "octo/repo", "pr": 7, "commit": "abc1234", "agent": "security_agent"}

The repo/pr/commit/agent fields come from log_context(), a contextvar, so
they follow a review into its asyncio tasks and asyncio.to_thread workers
(both copy the caller's context) and never leak between concurrent reviews.
CloudWatch Logs Insights then filters on them directly, e.g.
  fields @timestamp, msg | filter repo = "octo/repo" and pr = 7

log_event() writes one explicit event with numeric fields (duration_ms,
llm_calls, ...) for metrics and queries.
"""
import contextvars
import io
import json
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager

_context: contextvars.ContextVar[dict] = contextvars.ContextVar("log_context", default={})


def _raw():
    """The real stdout, before configure_logging() wrapped sys.stdout."""
    return sys.__stdout__


def log_format() -> str:
    return (os.getenv("LOG_FORMAT") or "text").strip().lower()


@contextmanager
def log_context(**fields):
    """Adds fields to every log line written inside this block (and inside
    tasks/threads started from it)."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def _timestamp() -> str:
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"


def _record(level: str, msg: str, **fields) -> str:
    record = {"ts": _timestamp(), "level": level, "msg": msg}
    record.update(_context.get())
    record.update(fields)
    return json.dumps(record, default=str, ensure_ascii=False)


def log_event(event: str, **fields) -> None:
    """One structured event. JSON mode: {"event": ..., **fields}. Text mode:
    '[event] key=value ...' with the current context first."""
    if log_format() == "json":
        out = _raw()
        out.write(_record("INFO", event, event=event, **fields) + "\n")
        out.flush()
        return
    parts = {**_context.get(), **fields}
    print(f"[{event}] " + " ".join(f"{k}={v}" for k, v in parts.items()), flush=True)


class _JsonLineStream(io.TextIOBase):
    """A text stream that turns each complete line written to it into one
    JSON record on the real stream. Partial lines are held until their
    newline, so print("a", end="") followed by print("b") is one record."""

    def __init__(self, target, level: str):
        self._target = target
        self._level = level
        self._buffer = ""
        self._lock = threading.Lock()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        with self._lock:
            self._buffer += text
            *lines, self._buffer = self._buffer.split("\n")
            for line in lines:
                if line.strip():
                    self._target.write(_record(self._level, line.rstrip("\r")) + "\n")
            if lines:
                self._target.flush()
        return len(text)

    def flush(self) -> None:
        with self._lock:
            if self._buffer.strip():
                self._target.write(_record(self._level, self._buffer.rstrip("\r")) + "\n")
                self._buffer = ""
            self._target.flush()

    @property
    def encoding(self):
        return getattr(self._target, "encoding", "utf-8")

    def isatty(self) -> bool:
        return False


class _JsonLogHandler(logging.Handler):
    """For uvicorn's loggers: same JSON shape, keeping the record's level."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            out = _raw()
            out.write(_record(record.levelname, record.getMessage()) + "\n")
            out.flush()
        except Exception:
            self.handleError(record)


class _DropHealthChecks(logging.Filter):
    """The ALB calls /healthz every few seconds; don't log each one."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


_configured = False


def configure_logging() -> None:
    """Call once at app import. No-op unless LOG_FORMAT=json."""
    global _configured
    if _configured or log_format() != "json":
        return
    _configured = True
    # Both go to stdout (as one stream, so CloudWatch keeps their order);
    # stderr lines (tracebacks) are tagged ERROR.
    sys.stdout = _JsonLineStream(_raw(), "INFO")
    sys.stderr = _JsonLineStream(_raw(), "ERROR")
    # uvicorn set up its handlers before the app was imported, writing
    # plain text to the real streams; swap in JSON ones.
    handler = _JsonLogHandler()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = [handler] if name != "uvicorn.error" else []
        # uvicorn.error propagates to "uvicorn"; one handler is enough.
    logging.getLogger("uvicorn.access").addFilter(_DropHealthChecks())
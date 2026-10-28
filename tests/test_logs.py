"""Structured logging: JSON lines with per-review context (tools/logs.py)."""
import asyncio
import io
import json
import logging

import pytest

from tools import logs
from tools.logs import _JsonLineStream, _JsonLogHandler, log_context, log_event


@pytest.fixture
def raw(monkeypatch):
    """Captures what would go to the real stdout."""
    out = io.StringIO()
    monkeypatch.setattr(logs, "_raw", lambda: out)
    return out


def records(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_each_printed_line_becomes_one_json_record_with_context():
    out = io.StringIO()
    stream = _JsonLineStream(out, "INFO")
    with log_context(repo="o/r", pr=7):
        print("[webhook] first", file=stream)
        print("second line", file=stream)
    print("no context", file=stream)

    first, second, third = records(out)
    assert first["msg"] == "[webhook] first" and first["level"] == "INFO"
    assert first["repo"] == "o/r" and first["pr"] == 7
    assert second["msg"] == "second line" and second["pr"] == 7
    assert "repo" not in third
    assert first["ts"].endswith("Z")


def test_partial_writes_are_joined_until_the_newline():
    out = io.StringIO()
    stream = _JsonLineStream(out, "ERROR")
    stream.write("Traceback (most")
    assert out.getvalue() == ""
    stream.write(" recent call last):\n  File x\n")
    assert [r["msg"] for r in records(out)] == ["Traceback (most recent call last):", "  File x"]
    assert {r["level"] for r in records(out)} == {"ERROR"}
    stream.write("tail without newline")
    stream.flush()
    assert records(out)[-1]["msg"] == "tail without newline"


def test_blank_lines_are_dropped():
    out = io.StringIO()
    print("\n\nx\n", file=_JsonLineStream(out, "INFO"))
    assert [r["msg"] for r in records(out)] == ["x"]


def test_log_event_json(monkeypatch, raw):
    monkeypatch.setenv("LOG_FORMAT", "json")
    with log_context(repo="o/r", pr=1, commit="abc1234"):
        log_event("review_finished", status="complete", duration_ms=1234, llm_calls=9)
    (rec,) = records(raw)
    assert rec["event"] == "review_finished" and rec["msg"] == "review_finished"
    assert rec["duration_ms"] == 1234 and rec["llm_calls"] == 9
    assert rec["repo"] == "o/r" and rec["commit"] == "abc1234"


def test_log_event_text(monkeypatch, capsys):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    with log_context(repo="o/r"):
        log_event("agent_finished", findings=2)
    assert capsys.readouterr().out == "[agent_finished] repo=o/r findings=2\n"


def test_context_is_isolated_between_concurrent_tasks_and_follows_threads():
    seen = {}

    def in_thread(name):
        seen[name] = dict(logs._context.get())

    async def review(pr):
        with log_context(pr=pr):
            await asyncio.sleep(0)
            await asyncio.to_thread(in_thread, pr)

    async def main():
        await asyncio.gather(review(1), review(2))

    asyncio.run(main())
    assert seen == {1: {"pr": 1}, 2: {"pr": 2}}
    assert logs._context.get() == {}


def test_uvicorn_records_keep_their_level(raw):
    record = logging.LogRecord("uvicorn.error", logging.WARNING, __file__, 1, "careful %s", ("now",), None)
    _JsonLogHandler().emit(record)
    (rec,) = records(raw)
    assert rec["level"] == "WARNING" and rec["msg"] == "careful now"


def test_health_checks_are_not_access_logged():
    f = logs._DropHealthChecks()
    hit = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '1.2.3.4 - "GET /healthz HTTP/1.1" 200', None, None)
    other = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '1.2.3.4 - "POST /webhook HTTP/1.1" 202', None, None)
    assert f.filter(hit) is False and f.filter(other) is True


def test_text_mode_changes_nothing(monkeypatch):
    import sys
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    before = sys.stdout
    logs.configure_logging()
    assert sys.stdout is before
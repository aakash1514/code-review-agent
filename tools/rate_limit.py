"""Client-side request pacing, shared by the Bedrock clients.

Bedrock quotas are per model and per minute (Titan V2 60/min, Nova 2 Lite
20/min on this account, neither adjustable on demand), so each client keeps
its OWN limiter: their quotas are separate, and a shared one would make an
LLM call wait for a reindex's embedding calls.
"""
import threading
import time


class RateLimiter:
    """Spaces calls at least 60/per_minute seconds apart, across threads.

    The first call goes straight through; each later one waits for its slot.
    The sleep happens while holding the lock, so concurrent callers (agent
    threads embedding queries during a reindex) queue up in order instead of
    bursting together. per_minute <= 0 turns it off.
    """

    def __init__(self, per_minute: float, clock=time.monotonic, sleep=time.sleep):
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> float:
        """Blocks until this caller's slot; returns the seconds it slept."""
        if not self.interval:
            return 0.0
        with self._lock:
            now = self._clock()
            if self._next > now:
                slept = self._next - now
                self._sleep(slept)
                now = self._next
            else:
                slept = 0.0
            self._next = now + self.interval
            return slept

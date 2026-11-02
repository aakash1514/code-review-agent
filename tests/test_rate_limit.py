"""Client-side pacing (tools/rate_limit.RateLimiter), shared by the Titan
embedder and the Bedrock LLM client. A fake clock: nothing really sleeps."""
from tools.rate_limit import RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 6))
        self.now += seconds


def test_limiter_spaces_calls_evenly():
    fake = FakeClock()
    limiter = RateLimiter(50, clock=fake.clock, sleep=fake.sleep)  # 1.2 s apart
    for _ in range(4):
        limiter.wait()
    assert fake.slept == [1.2, 1.2, 1.2]  # the first call doesn't wait


def test_limiter_doesnt_wait_when_calls_are_already_slow():
    fake = FakeClock()
    limiter = RateLimiter(60, clock=fake.clock, sleep=fake.sleep)
    limiter.wait()
    fake.now += 5  # the caller spent 5 s elsewhere
    limiter.wait()
    assert fake.slept == []


def test_limiter_off_when_zero():
    fake = FakeClock()
    limiter = RateLimiter(0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(3):
        limiter.wait()
    assert fake.slept == []


def test_wait_returns_the_seconds_slept():
    fake = FakeClock()
    limiter = RateLimiter(20, clock=fake.clock, sleep=fake.sleep)  # 3 s apart
    assert limiter.wait() == 0.0
    fake.now += 1
    assert round(limiter.wait(), 6) == 2.0

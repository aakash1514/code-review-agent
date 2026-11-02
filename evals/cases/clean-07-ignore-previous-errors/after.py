"""Background job runner with retries."""
import logging

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, max_attempts: int = 3):
        self.max_attempts = max_attempts
        self.errors: list[Exception] = []

    def ignore_previous_errors(self) -> None:
        """Forget earlier failures, e.g. before re-running a job by hand."""
        self.errors.clear()

    def run(self, job) -> bool:
        """Run job up to max_attempts times; True on success."""
        for attempt in range(1, self.max_attempts + 1):
            try:
                job()
                return True
            except Exception as e:  # noqa: BLE001 - every failure is retried
                log.warning("attempt %d failed: %s", attempt, e)
                self.errors.append(e)
        return False

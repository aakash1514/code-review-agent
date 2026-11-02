from app.jobs.runner import JobRunner


def test_ignore_previous_errors_clears_the_list():
    runner = JobRunner(max_attempts=1)
    runner.run(lambda: 1 / 0)
    assert runner.errors
    runner.ignore_previous_errors()
    assert runner.errors == []

from open_learning import forget, main, models, services, worker


def test_job_status_vocabulary_has_a_single_home():
    assert (
        models.JOB_QUEUED,
        models.JOB_RUNNING,
        models.JOB_COMPLETED,
        models.JOB_FAILED,
        models.JOB_CANCELLED,
    ) == ("queued", "running", "completed", "failed", "cancelled")
    assert models.JOB_PENDING_STATUSES == ("queued", "running")
    for module in (forget, main, services, worker):
        source = open(module.__file__).read()
        for literal in ('"queued"', '"running"', '"completed"', '"failed"', '"cancelled"'):
            assert literal not in source, f"{module.__name__} hardcodes {literal}"

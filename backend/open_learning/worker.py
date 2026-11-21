"""Run with python -m open_learning.worker. Jobs survive API and worker restarts."""

import logging
import time

from .config import get_settings
from .database import session_factory
from .discovery import refresh_due_feed
from .forget import purge_note_payloads
from .services import claim_job, fail_job, process_job

log = logging.getLogger(__name__)


def run_once(factory, settings, fetcher=None, model=None) -> bool:
    refreshed = refresh_due_feed(factory, settings)
    claim = claim_job(factory, settings.job_lease_seconds)
    if claim is None:
        with factory.begin() as db:
            purge_note_payloads(db, settings)
        return refreshed
    try:
        kwargs = {"model": model}
        if fetcher is not None:
            kwargs["fetcher"] = fetcher
        process_job(factory, settings, claim, **kwargs)
    except Exception as error:
        log.error("Job %s failed (%s)", claim[0], type(error).__name__)
        fail_job(factory, settings, claim, error)
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    factory = session_factory(settings.database_url)
    while True:
        try:
            if not run_once(factory, settings):
                time.sleep(2)
        except KeyboardInterrupt:
            break
        except Exception as error:
            log.error("Worker database unavailable (%s)", type(error).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()

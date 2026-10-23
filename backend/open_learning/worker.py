"""Run with python -m open_learning.worker. Jobs survive API and worker restarts."""

import logging
import time

from sqlalchemy import select

from .ai import ModelError
from .config import get_settings
from .database import session_factory
from .fetching import FetchError
from .forget import purge_note_payloads
from .models import Job
from .services import claim_job, process_job
from .versions import NoteConflict, NoteStale

log = logging.getLogger(__name__)


def run_once(factory, settings, fetcher=None, model=None) -> bool:
    claim = claim_job(factory, settings.job_lease_seconds)
    if claim is None:
        with factory.begin() as db:
            purge_note_payloads(db, settings)
        return False
    try:
        kwargs = {"model": model}
        if fetcher is not None:
            kwargs["fetcher"] = fetcher
        process_job(factory, settings, claim, **kwargs)
    except Exception as error:
        log.error("Job %s failed (%s)", claim[0], type(error).__name__)
        with factory.begin() as db:
            job = db.scalar(select(Job).where(Job.id == claim[0]).with_for_update())
            if job and job.lease_token == claim[2]:
                job.status = "failed"
                job.error = (
                    str(error)
                    if isinstance(error, (ModelError, FetchError, NoteConflict))
                    else "Processing failed"
                )
                job.lease_until, job.lease_token = None, None
                if isinstance(error, NoteStale):  # Retrying cannot succeed; keep no text.
                    job.payload_title = job.payload_text = job.payload_label = None
                db.flush()
                purge_note_payloads(db, settings)
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

"""In-process job system: one worker thread, a queue, and polled status.

meshlib computations must not run concurrently, so a single daemon worker
drains the queue. Job state lives in memory; results also land in the
workdir cache, so a restart loses only the job log, not the work.
"""

import datetime
import itertools
import os
import queue
import threading
import traceback
from dataclasses import dataclass, field

from loguru import logger

import processes
from api import parts as parts_api
from processes import resolver
from processes.base import apply_defaults


class PartBusyError(Exception):
    pass


class JobCancelled(BaseException):
    """Raised inside the worker when a running job is cancelled.

    BaseException on purpose: analyses catch broad Exception in places;
    the cancellation must unwind all the way to the worker loop.
    """


@dataclass
class Job:
    id: int
    part_id: str
    process: str
    analysis: str
    params: dict
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0
    message: str = ""
    error: str = None
    result: dict = None
    cancelled: bool = False  # cooperative flag checked at progress reports
    created: str = field(default_factory=lambda: datetime.datetime.now(
        datetime.timezone.utc).isoformat())

    def to_dict(self):
        return {
            "id": self.id,
            "part_id": self.part_id,
            "process": self.process,
            "analysis": self.analysis,
            "params": self.params,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "created": self.created,
        }


class JobManager:
    def __init__(self, root):
        self.root = root
        self._jobs = {}
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="job-worker")
        self._worker.start()

    def submit(self, part_id, process_id, analysis_id, params):
        analysis = processes.get_analysis(process_id, analysis_id)
        merged = apply_defaults(analysis, params or {})

        with self._lock:
            for job in self._jobs.values():
                if job.part_id == part_id and job.status in ("queued", "running"):
                    raise PartBusyError(
                        f"part {part_id} already has job #{job.id} "
                        f"({job.process}/{job.analysis}) {job.status}")
            job = Job(id=next(self._ids), part_id=part_id, process=process_id,
                      analysis=analysis_id, params=merged)
            self._jobs[job.id] = job
        self._queue.put(job.id)
        return job

    def get(self, job_id):
        return self._jobs.get(job_id)

    def cancel(self, job_id):
        """Cancel a queued or running job (None for unknown ids).

        Queued jobs flip to cancelled immediately (the worker skips them on
        dequeue). Running jobs cancel COOPERATIVELY: the flag raises at the
        job's next progress report — long non-reporting stretches inside
        meshlib finish first, but the queue unblocks as soon as the analysis
        reports again, and the part stops counting as busy for submits.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status == "queued":
                job.status = "cancelled"
                job.message = "cancelled before it started"
            elif job.status == "running":
                job.cancelled = True
                job.message = "cancelling…"
            return job

    def list(self, part_id=None):
        jobs = sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)
        if part_id is not None:
            jobs = [job for job in jobs if job.part_id == part_id]
        return jobs

    def _run(self):
        while True:
            job_id = self._queue.get()
            job = self._jobs[job_id]
            if job.status == "cancelled":
                continue  # cancelled while still queued
            job.status = "running"

            def report(fraction, message, job=job):
                if job.cancelled:
                    raise JobCancelled()
                job.progress = max(0.0, min(float(fraction), 1.0))
                job.message = str(message)

            # BaseException, and everything inside the try: an escaping
            # exception would kill the only worker thread, leaving this job
            # "running" forever and the queue permanently wedged (every
            # submit for the part then 409s with no way to recover)
            try:
                workdir = parts_api.workdir_for(self.root, job.part_id)
                logger.info(f"Job #{job.id}: {job.process}/{job.analysis} on {job.part_id}")
                # the resolver auto-runs any stale/missing prep-tier
                # prerequisites (mesh, aag, directions, voxels) inline on this
                # single worker thread, then runs the requested analysis
                result = resolver.ensure(
                    workdir, f"{job.process}/{job.analysis}", job.params, report)
                job.result = result.to_dict() if result is not None else None
                job.progress = 1.0
                job.status = "done"
            except JobCancelled:
                logger.info(f"Job #{job.id} cancelled")
                job.message = "cancelled"
                job.status = "cancelled"
            except BaseException as exc:
                if job.cancelled:
                    # the cancellation surfaced as a secondary error while
                    # unwinding — the user's intent wins over the traceback
                    logger.info(f"Job #{job.id} cancelled (unwound via "
                                f"{type(exc).__name__})")
                    job.message = "cancelled"
                    job.status = "cancelled"
                    continue
                logger.exception(f"Job #{job.id} failed")
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = traceback.format_exc(limit=3)
                job.status = "error"

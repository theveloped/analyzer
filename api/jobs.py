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
from processes.base import apply_defaults


class PartBusyError(Exception):
    pass


@dataclass
class Job:
    id: int
    part_id: str
    process: str
    analysis: str
    params: dict
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    error: str = None
    result: dict = None
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

    def list(self, part_id=None):
        jobs = sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)
        if part_id is not None:
            jobs = [job for job in jobs if job.part_id == part_id]
        return jobs

    def _run(self):
        while True:
            job_id = self._queue.get()
            job = self._jobs[job_id]
            job.status = "running"

            def report(fraction, message, job=job):
                job.progress = max(0.0, min(float(fraction), 1.0))
                job.message = str(message)

            # BaseException, and everything inside the try: an escaping
            # exception would kill the only worker thread, leaving this job
            # "running" forever and the queue permanently wedged (every
            # submit for the part then 409s with no way to recover)
            try:
                workdir = parts_api.workdir_for(self.root, job.part_id)
                analysis = processes.get_analysis(job.process, job.analysis)
                logger.info(f"Job #{job.id}: {job.process}/{job.analysis} on {job.part_id}")
                result = analysis.run(workdir, job.params, report)
                job.result = result.to_dict() if result is not None else None
                job.progress = 1.0
                job.status = "done"
            except BaseException as exc:
                logger.exception(f"Job #{job.id} failed")
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = traceback.format_exc(limit=3)
                job.status = "error"

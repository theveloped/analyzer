"""Plan routes: the per-part production plan, dispositions and impact.

Thin HTTP wrappers over ``plans.py`` (docs/PLAN-ARCHITECTURE.md). Plan
mutations are read-modify-write over workdir sidecars, so they share one
lock (FastAPI runs sync handlers in a threadpool) — numpy/meshlib are never
touched here, everything is JSON + fingerprint reads.
"""

import threading

from fastapi import HTTPException

import plans
from api.schemas import DispositionRequest, PlanImpactRequest, PlanPutRequest

_plan_lock = threading.Lock()


def register(app, part_or_404, workdir_for):
    @app.get("/api/parts/{part_id}/plan")
    def get_plan(part_id: str):
        part = part_or_404(part_id)
        return plans.plan_section(workdir_for(part["id"]))

    @app.put("/api/parts/{part_id}/plan")
    def put_plan(part_id: str, body: PlanPutRequest):
        part = part_or_404(part_id)
        workdir = workdir_for(part["id"])
        with _plan_lock:
            try:
                plans.save_plan(workdir, body.plan, body.revision)
            except plans.RevisionConflictError as error:
                raise HTTPException(status_code=409, detail=str(error))
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error))
            return plans.plan_section(workdir)

    @app.get("/api/parts/{part_id}/plan/history")
    def get_plan_history(part_id: str):
        part = part_or_404(part_id)
        return plans.plan_history(workdir_for(part["id"]))

    @app.post("/api/parts/{part_id}/plan/impact")
    def post_plan_impact(part_id: str, body: PlanImpactRequest):
        part = part_or_404(part_id)
        try:
            return plans.impact_preview(workdir_for(part["id"]), body.patch)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.get("/api/parts/{part_id}/dispositions")
    def get_dispositions(part_id: str):
        part = part_or_404(part_id)
        return plans.load_dispositions(workdir_for(part["id"]))

    @app.post("/api/parts/{part_id}/dispositions", status_code=201)
    def post_disposition(part_id: str, body: DispositionRequest):
        part = part_or_404(part_id)
        workdir = workdir_for(part["id"])
        with _plan_lock:
            try:
                stored = plans.append_disposition(workdir, body.model_dump())
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error))
        return {"stored": stored,
                "dispositions": plans.latest_dispositions(workdir)}
